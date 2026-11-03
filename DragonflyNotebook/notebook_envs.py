r"""Custom Python environments for the notebook (round 16).

One notebook window can run its kernel on a private virtual environment
instead of Dragonfly's embedded Python. This module owns everything
about those environments EXCEPT the UI: where they live, what counts as
one, how they are created, how packages are installed into them, and
when one is "in use" (the runner that executes the environment's cells
is ``dfnb_env_kernel``; the kernel module owns its lifecycle).

The layout follows the registry's folder convention
(``%LOCALAPPDATA%\\DragonflyNotebook``): every environment is a full
``python -m venv`` tree under ``%LOCALAPPDATA%\\DragonflyNotebook\\
environments\\<name>``. The venv is ISOLATED (no ``--system-site-
packages``), and the isolation is PROCESS-DEEP: a notebook with an
environment selected runs its CELLS in a subprocess on the venv's OWN
interpreter (see ``dfnb_env_kernel``, the runner), while the notebook
application (the GUI, the embedded IPython kernel, the bridge client)
stays on the embedded Python no matter what is selected. A ``pip
install`` into the venv upgrades exactly the packages it names,
ABI-coherent inside the venv, and the embedded distribution is never
touched. (Round 16 first shipped an in-process overlay instead, and it
failed in live use: with the venv's site-packages in front of
``sys.path`` and the embedded site-packages still on it, an embedded
extension module loaded against the venv's fresh numpy and failed its
ABI checks, and the breakage SURVIVED switching back to the embedded
Python because a loaded extension cannot be un-bound. Only a process
boundary makes the switch honest.)

No console windows, ever. The notebook application is a DETACHED
process; a console-less parent that spawns an ordinary child makes that
child allocate a NEW VISIBLE console (the round-13/14 window-spam
lesson), and ``python -m venv`` itself spawns a pip grandchild through
ensurepip, and pip spawns its own children besides. So BOTH long runs
(venv creation, pip install) start as ``<python> -c <bootstrap>`` where
the bootstrap patches the CHILD's own ``subprocess.Popen.__init__`` to
default CREATE_NO_WINDOW before runpy-running the real module
(``venv`` / ``pip``); every grandchild in the tree inherits the patch
and allocates no console. ``popen`` below adds CREATE_NO_WINDOW to the
direct child too. Probed on the embedded interpreter: creation and a
pip install both complete through the bootstrap with zero new visible
windows, and venv's own pip (bootstrapped by ensurepip) works for
installs afterwards.

In use. An environment is "in use" while SOME notebook kernel is
running on it. The signal is an exclusive ``msvcrt.locking`` byte lock
on ``<venv>\\.dfnb_in_use``: the kernel's worker thread acquires it
when it starts on the environment and releases it at kernel shutdown;
if the window dies, the OS releases the lock when the process exits
(the marker FILE staying behind is harmless, the LOCK is the signal).
Two lookups answer "is it in use": this process's held locks live in a
module dict (the form's delete flow and the kernel worker run in the
same process as the lock taker, and msvcrt's same-process relock
behavior must not decide the UX), and other windows are other
PROCESSES, detected by a try-lock-and-release probe. Round 18 moved
the consumers off the pip button (a cell run leaves its kernel
RUNNING, so a lock-based disable there was permanent): the lock now
gates DELETION -- the form stops its own kernel first (its hold is in
the dict) and refuses only another window's OS-lock hold -- and the
pip tooltip's residual-risk note (a running kernel keeps its DLLs
loaded; a pip command that replaces them can fail with a Windows
file-in-use error).

The subprocess model (one numpy copy per PROCESS). The kernel's lazy
start with an environment selected spawns the venv's own interpreter
with ``dfnb_env_kernel.py`` (a pure-stdlib runner; the creation used
the STOCK interpreter, so the venv's ``Scripts`` copy is manifest-clean)
and the cells execute THERE, on the venv's ``sys.path`` (its own
site-packages plus the embedded stdlib through ``pyvenv.cfg``'s
``home``). ``dfnb_client`` and ``notebook_bridge_protocol`` still bind
numpy LAZILY on the application side (the DLL-directory registration
must precede the first import there), and the values that cross the
boundary (dfnb arrays, DataFrames) travel as ``numpy.save`` BYTES, so
the two processes' numpy copies never meet and no ABI identity is ever
shared across it. ``DFNB_ENVS_ROOT`` (an environment variable)
overrides the environments root: the test seams (the staged tier-3
suite) point it at a temp tree so their real-venv pins never touch the
user's.

Nothing in this module imports Qt, numpy or ORS: it is importable by
the kernel worker, by the form, and by the framework-free tier-1 suite.
"""

import logging
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys

try:
    import msvcrt  # Windows-only; dev hosts run without it
except ImportError:
    msvcrt = None

__all__ = [
    'EMBEDDED_LABEL', 'NEW_ENV_LABEL', 'NEW_ENV_SENTINEL',
    'DELETE_ENV_LABEL', 'DELETE_ENV_SENTINEL', 'BASE_PACKAGES',
    'envs_root', 'env_dir', 'venv_site_packages', 'venv_python',
    'validate_name', 'list_environments',
    'in_use_marker_path', 'acquire_in_use', 'is_environment_in_use',
    'release_in_use', 'ensure_runtime_dlls',
    'embedded_python', 'venv_creation_command', 'pip_command',
    'pip_install_command',
    'popen',
]

#: The combo's default entry: Dragonfly's own interpreter (the behavior
#: before custom environments existed, still the default for a fresh
#: notebook).
EMBEDDED_LABEL = 'Dragonfly embedded Python'

#: The combo's last entry: opens the New Environment dialog.
NEW_ENV_LABEL = '+ New Environment'

#: The userData of the "+ New Environment" combo entry (a value no
#: environment name can collide with: names start with a letter or
#: digit).
NEW_ENV_SENTINEL = '__dfnb_new_environment__'

#: The combo's delete entry (round 17: a minus entry that removes
#: existing environments), placed after the plus entry.
DELETE_ENV_LABEL = '- Delete Environment'

#: The userData of the delete entry (same collision-freedom as the plus).
DELETE_ENV_SENTINEL = '__dfnb_delete_environment__'

#: What a fresh environment gets beyond the bare interpreter (round 17's
#: contract): numpy and pandas are the minimum the notebook's
#: own data paths need -- volumes cross as numpy arrays and measured
#: properties as DataFrames -- so creation installs them before the
#: environment is first selected. matplotlib and anything heavier stay a
#: pip button away (round 17: pip runs from the button, never from
#: cells): the minimal set, not a distribution.
BASE_PACKAGES = ('numpy', 'pandas')

#: Names the Windows loader resolves by schema, never as files to copy
#: (every extension module's import table carries them; they appear in
#: the scan below and must not reach the copy set).
_API_SET_PREFIXES = ('api-ms-', 'ext-ms-')

# The app's logger tree (mainform logs as 'DragonflyNotebook'; these
# records are the delivery breadcrumbs, see ensure_runtime_dlls).
_LOGGER = logging.getLogger('DragonflyNotebook.envs')

# The per-machine misparked set, cached per base tree: scanning the
# base's extension modules is read-only work and the tree does not
# change under a running application (round 17's follow-up probe ran
# the scan, copied what it found, and healed both machine-misparked
# modules -- sqlite3 AND tkinter's tcl86t -- while everything the
# python runtime already carries kept importing even PATH-clean).
_RUNTIME_SCAN_CACHE = {}

_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')

# CREATE_NO_WINDOW for every child this module spawns; 0 elsewhere
# (POSIX has no console to allocate).
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# The no-window bootstrap (see the module docstring): run as
# ``<python> -c <bootstrap> <module> [args...]``. The child patches its
# own subprocess.Popen so every grandchild is CREATE_NO_WINDOW'd, then
# runpy-runs ``<module>`` as ``__main__`` with ``sys.argv[0]`` set to
# the module name (venv's and pip's ``__main__`` read ``sys.argv[1:]``,
# exactly as under ``python -m``). A ``sys.exit()`` inside the module
# propagates through runpy and sets the child's exit code.
_NO_WINDOW_BOOTSTRAP = (
    'import subprocess, sys\n'
    '_init = subprocess.Popen.__init__\n'
    'def _no_window_init(self, *args, **kwargs):\n'
    '    if sys.platform == "win32":\n'
    '        kwargs["creationflags"] = (kwargs.get("creationflags", 0) '
    '| 0x08000000)\n'
    '    return _init(self, *args, **kwargs)\n'
    'subprocess.Popen.__init__ = _no_window_init\n'
    'module_name = sys.argv[1]\n'
    'sys.argv = [module_name] + list(sys.argv[2:])\n'
    'import runpy\n'
    'runpy.run_module(module_name, run_name="__main__")\n')


# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------

def envs_root():
    """Where the environments live:
    ``%LOCALAPPDATA%\\DragonflyNotebook\\environments`` (the registry's
    folder convention), overridable through ``DFNB_ENVS_ROOT`` (the
    test seam, see the module docstring)."""
    override = os.environ.get('DFNB_ENVS_ROOT')
    if override:
        return override
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        base = os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
    return os.path.join(base, 'DragonflyNotebook', 'environments')


def env_dir(root, name):
    """The venv directory of one environment."""
    return os.path.join(root, name)


def venv_site_packages(venv_dir):
    """The venv's site-packages directory (the Windows venv layout on
    win32, the CPython layout elsewhere so the module stays importable
    on dev hosts)."""
    if os.name == 'nt':
        return os.path.join(venv_dir, 'Lib', 'site-packages')
    return os.path.join(venv_dir, 'lib',
                        'python{}.{}'.format(*sys.version_info[:2]),
                        'site-packages')


def venv_python(venv_dir):
    """The venv's own interpreter (the pip install child)."""
    if os.name == 'nt':
        return os.path.join(venv_dir, 'Scripts', 'python.exe')
    return os.path.join(venv_dir, 'bin', 'python')


# ---------------------------------------------------------------------------
# names and discovery
# ---------------------------------------------------------------------------

def validate_name(name, root):
    """One environment name through the rules: a nonempty string of
    letters, digits, ``.``, ``_`` and ``-`` (at most 64 characters,
    starting with a letter or digit) that no existing environment
    already uses. Returns the stripped name; raises ``ValueError`` with
    an actionable message otherwise (the New Environment dialog shows
    it live, the creation path re-checks it)."""
    candidate = (name or '').strip()
    if not candidate:
        raise ValueError('The environment name cannot be empty.')
    if not _NAME_RE.match(candidate):
        raise ValueError("The environment name may use letters, digits, "
                         "'.', '_' and '-' (at most 64 characters, "
                         'starting with a letter or digit).')
    if os.path.exists(env_dir(root, candidate)):
        raise ValueError("An environment named '{}' already exists."
                         .format(candidate))
    return candidate


def list_environments(root):
    """The sorted environment names under ``root``. A directory counts
    as an environment only when it carries ``pyvenv.cfg`` and the venv
    interpreter: a half-created or hand-dropped folder is skipped (the
    combo must never offer a venv that cannot run pip)."""
    if not os.path.isdir(root):
        return []
    names = []
    for entry in os.listdir(root):
        candidate = env_dir(root, entry)
        if (os.path.isdir(candidate)
                and os.path.isfile(os.path.join(candidate, 'pyvenv.cfg'))
                and os.path.isfile(venv_python(candidate))):
            names.append(entry)
    return sorted(names, key=str.lower)


# ---------------------------------------------------------------------------
# the in-use lock
# ---------------------------------------------------------------------------

def in_use_marker_path(venv_dir):
    """The lock marker inside a venv: ``<venv>\\.dfnb_in_use`` (a name
    no environment can collide with; the FILE's existence means
    nothing, the LOCK on it does)."""
    return os.path.join(venv_dir, '.dfnb_in_use')


# this process's held locks: marker path -> os fd. Same-process holders
# are decided by this dict (not by re-locking: the pip button runs in
# the same process as its own kernel).
_in_use_fds = {}


def acquire_in_use(venv_dir):
    """Marks the environment in use: an exclusive ``msvcrt`` lock on
    the marker, held until :func:`release_in_use` or process exit
    (crash-safe: the OS closes the descriptor, which frees the lock).
    Returns the marker path. Raises ``RuntimeError`` when the
    environment is already held (this window: the module dict; another
    window: the OS lock), and ``OSError`` when the marker cannot even
    be created (a read-only disk, say)."""
    path = in_use_marker_path(venv_dir)
    if path in _in_use_fds:
        raise RuntimeError(
            "The environment '{}' is already in use in this notebook "
            'window.'.format(os.path.basename(venv_dir)))
    fd = os.open(path, os.O_CREAT | os.O_RDWR)
    try:
        os.write(fd, b'x')
        os.lseek(fd, 0, os.SEEK_SET)
        if msvcrt is not None:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        os.close(fd)
        raise RuntimeError(
            "The environment '{}' is in use by another notebook window. "
            'Close that window (or switch its environment) before using '
            'this one. ({})'.format(os.path.basename(venv_dir), exc))
    _in_use_fds[path] = fd
    return path


def is_environment_in_use(venv_dir):
    """True while any kernel holds the environment: this process (the
    module dict) or another notebook window (a try-lock probe on the
    marker). Never raises and never leaves a lock behind: a probe that
    cannot acquire was held by someone else, a probe that can is
    immediately released. On dev hosts without msvcrt only the
    same-process dict can answer."""
    path = in_use_marker_path(venv_dir)
    if path in _in_use_fds:
        return True
    if msvcrt is None or not os.path.isfile(path):
        return False
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        # unreadable marker: no lock of ours can be involved
        return False
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        # held by another window: release nothing (the lock is not
        # ours), just close the probe's descriptor
        os.close(fd)
        return True
    try:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)
    return False


def release_in_use(venv_dir):
    """Releases this window's hold (True when there was one). Never
    raises: the teardown paths call it inside best-effort blocks, and a
    failed unlock anyway ends with the process exit, which the OS
    treats as a release."""
    path = in_use_marker_path(venv_dir)
    fd = _in_use_fds.pop(path, None)
    if fd is None:
        return False
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if msvcrt is not None:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass
    return True


# ---------------------------------------------------------------------------
# the long runs (creation, pip install)
# ---------------------------------------------------------------------------

def embedded_python():
    """The STOCK interpreter of the running distribution:
    ``sys.base_prefix``'s ``python.exe`` (the fallback is
    ``sys.executable``).

    The notebook application process runs as one of Dragonfly's licensed
    launchers (``pythonnoconsole.exe``), whose embedded manifest declares a
    ``dependentAssembly`` on the QlmLicenseLib side-by-side assembly
    (resolved by ``QlmLicenseLib.manifest`` / ``QlmLicenseLib.dll`` beside
    it in the Python_env root). ``venv`` COPIES the creating exe into the
    venv's ``Scripts`` and runs the copy for ensurepip; the copy sits
    without the license assembly beside it, so Windows cannot build its
    activation context and the grandchild dies with ``[WinError 14001]``
    (probe-reproduced on this machine, tmp/probe_14001.py). The stock
    ``python.exe`` carries no such dependency and the venv it creates
    carries only the stock exes, so EVERY environment child runs through
    it."""
    candidate = os.path.join(sys.base_prefix, 'python.exe')
    if os.path.isfile(candidate):
        return candidate
    return sys.executable


def venv_creation_command(venv_dir):
    """The argv that creates a new venv at ``venv_dir`` with the
    distribution's STOCK interpreter (``embedded_python``; the licensed
    launcher that hosts the notebook application must not be the creating
    exe, see :func:`embedded_python`): the no-window bootstrap running the
    ``venv`` module (ensurepip's pip grandchild inherits the patch). Takes
    ~40 s on this machine (pip and setuptools are bootstrapped into the
    venv by ensurepip)."""
    return [embedded_python(), '-c', _NO_WINDOW_BOOTSTRAP,
            'venv', venv_dir]


def _launcher_is_pip(word):
    """True for the pip launcher spellings a venv's Scripts directory
    carries (``pip``, ``pip3``, ``pip3.11``): the pip button's field
    takes the FULL command, so the user may type any of them first."""
    return word == 'pip' or word.startswith('pip3')


def _split_command_words(text):
    """One command line into words the way the runner splits ``!``
    lines: nt splitting keeps Windows backslashes verbatim (posix
    splitting would eat them) and strips one matching quote pair per
    token, so quoted spans stay one word; posix splitting otherwise."""
    if os.name == 'nt':
        try:
            tokens = shlex.split(text, posix=False)
        except ValueError:
            return None
        words = []
        for token in tokens:
            if (len(token) >= 2 and token[0] == token[-1]
                    and token[0] in ('"', "'")):
                token = token[1:-1]
            words.append(token)
        return words
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return None


def pip_command_words(command):
    """The pip button's field into pip's own argument words: the
    launcher spellings the user may paste (``pip``, ``pip3``,
    ``python -m pip``) are stripped, so both the full ``pip install
    yfinance`` and the bare ``install yfinance`` spellings work;
    ``update``/``upgrade`` -- pip carries no update subcommand -- runs
    as ``install --upgrade``; and ``uninstall`` gains ``-y`` when the
    user left it off (``--no-input`` turns pip's confirmation question
    into a hard error, probe-proven on pip 23.0.1, and the dialog's
    child has no stdin to answer with). ``command`` is the text as
    typed or a ready word list. Raises ValueError when no pip command
    remains (a bare ``pip``)."""
    if isinstance(command, str):
        words = _split_command_words(command) or []
    else:
        words = [str(word) for word in command]
    if (len(words) > 2 and words[0] in ('python', 'python3')
            and words[1] == '-m' and _launcher_is_pip(words[2])):
        words = words[3:]
    if words and _launcher_is_pip(words[0]):
        words = words[1:]
    if not words:
        raise ValueError('no pip command in {!r}'.format(command))
    if words[0] in ('update', 'upgrade'):
        words = ['install', '--upgrade'] + words[1:]
    if words[0] == 'uninstall' and not any(
            word in ('-y', '--yes') for word in words[1:]):
        words = words + ['-y']
    return words


def pip_command(venv_dir, command):
    """The argv that runs ONE pip command (the pip button's field: the
    user types the full command, ``pip install yfinance``) with the
    venv's own pip: the VENV's interpreter (so the install lands in the
    venv) through the no-window bootstrap, ``--no-input`` and the
    version check disabled in front of the subcommand and its
    arguments (:func:`pip_command_words`)."""
    argv = [venv_python(venv_dir), '-c', _NO_WINDOW_BOOTSTRAP,
            'pip', '--no-input', '--disable-pip-version-check']
    argv.extend(pip_command_words(command))
    return argv


def pip_install_command(venv_dir, specs):
    """The install spelling of :func:`pip_command` (kept as its own
    contract: the creation flow's base-package install and the tier-3
    install pins call it with ready word lists, ``['install'] +
    specs``)."""
    return pip_command(venv_dir, ['install'] + [str(spec)
                                               for spec in specs])


def _venv_base_prefix(venv_dir):
    """The BASE interpreter directory this venv points at through its
    ``pyvenv.cfg`` (``home = ...``), falling back to this process's own
    base when the file is missing or silent -- the venvs this package
    creates always point home at the embedded interpreter, so the two
    agree everywhere the product runs."""
    cfg = os.path.join(venv_dir, 'pyvenv.cfg')
    try:
        with open(cfg, encoding='utf-8') as handle:
            for line in handle:
                key, _, value = line.partition('=')
                if key.strip() == 'home':
                    return value.strip()
    except (OSError, UnicodeDecodeError):
        pass
    return sys.base_prefix


def _import_table_names(path):
    """The DLL names in one PE file's import table (a pure ``struct``
    parse; an unreadable or unparseable file contributes nothing -- the
    scan is best-effort by design, and a DLL the scan cannot read stays
    simply uncopied)."""
    try:
        with open(path, 'rb') as handle:
            data = handle.read()
        pe = struct.unpack_from('<I', data, 0x3C)[0]
        if data[pe:pe + 4] != b'PE\x00\x00':
            return []
        nsec = struct.unpack_from('<H', data, pe + 6)[0]
        opt_size = struct.unpack_from('<H', data, pe + 20)[0]
        opt = pe + 24
        magic = struct.unpack_from('<H', data, opt)[0]
        ddir = opt + (112 if magic == 0x20B else 96)
        import_rva = struct.unpack_from('<II', data, ddir + 8)[0]
        if import_rva == 0:
            return []
        sections = []
        sec0 = opt + opt_size
        for index in range(nsec):
            off = sec0 + 40 * index
            _vsize, vaddr, _rsize, raddr = struct.unpack_from(
                '<IIII', data, off + 8)
            sections.append((vaddr, _vsize, raddr, _rsize))

        def rva2off(rva):
            for vaddr, vsize, raddr, rsize in sections:
                if vaddr <= rva < vaddr + max(vsize, rsize):
                    return raddr + (rva - vaddr)
            return None

        names = []
        off = rva2off(import_rva)
        if off is None:
            return []
        while True:
            ilt, _ts, _fc, name_rva, _iat = struct.unpack_from(
                '<IIIII', data, off)
            if name_rva == 0 and ilt == 0:
                break
            n_off = rva2off(name_rva)
            if n_off is None:
                break
            end = data.index(b'\x00', n_off)
            names.append(data[n_off:end].decode('ascii'))
            off += 20
        return names
    except Exception:
        return []


def _missing_runtime_dlls(base_dir):
    """Every dependent DLL the base interpreter's extension modules
    need that the base's own ``DLLs`` directory does not carry, closed
    TRANSITIVELY through the DLLs the scan finds (a copied DLL's own
    dependencies must be in the copy set too -- including a dependency
    that sits in the base ``DLLs`` directory: that directory is
    reachable for a pyd loading from it, but NOT for the copy, whose
    loader searches its own new directory, so such a dependency is
    copied as well). Returns a mapping of DLL name to the source file
    that carries it -- the python runtime's ``Library\\bin`` first (the
    ABI-matched tree the extension modules were built against), then
    the base root beside the interpreter (where a DLL parked next to
    python.exe resolves), with Dragonfly's ``libs`` tree as the
    fallback for the DLLs this distribution parks only there -- and
    maps a name to None when no source anywhere carries it. The
    loader-resolved names are excluded: API sets (``api-ms-*`` /
    ``ext-ms-*``, resolved by schema) and the ``python3*.dll`` family
    (already loaded by name when any extension module loads, so the
    loader never searches for them). Not name-keyed: whatever a future
    distribution build parks outside the base tree lands in this set
    automatically."""
    cached = _RUNTIME_SCAN_CACHE.get(base_dir)
    if cached is not None:
        return cached
    found = {}
    dlls_dir = os.path.join(base_dir, 'DLLs')
    if not os.path.isdir(dlls_dir):
        _RUNTIME_SCAN_CACHE[base_dir] = found
        return found
    system32 = os.path.join(
        os.environ.get('SystemRoot', r'C:\Windows'), 'System32')
    runtime_dirs = (os.path.join(base_dir, 'Library', 'bin'),
                    base_dir,
                    os.path.join(os.path.dirname(base_dir), 'libs'))
    present = {name.lower() for name in os.listdir(dlls_dir)}

    def excluded(name):
        low = name.lower()
        return (low.startswith(_API_SET_PREFIXES)
                or (low.startswith('python3') and low.endswith('.dll')))

    def resolve(name):
        for runtime_dir in runtime_dirs:
            candidate = os.path.join(runtime_dir, name)
            if os.path.isfile(candidate):
                return candidate
        return None

    # (file, copied): a base-DLLs file loads from its own directory, a
    # source from the runtime trees is destined for the copy -- only
    # the second kind pulls further names in as copies
    worklist = [(os.path.join(dlls_dir, name), False)
                for name in os.listdir(dlls_dir)
                if name.lower().endswith(('.pyd', '.dll'))]
    while worklist:
        path, copied = worklist.pop()
        for dep in _import_table_names(path):
            low = dep.lower()
            if excluded(dep) or low in found:
                continue
            if low in present and not copied:
                continue  # this file loads from its own directory
            if os.path.isfile(os.path.join(system32, dep)):
                continue
            if copied and low in present:
                # the copy's loader never reaches the base DLLs dir
                # (round 17 review): the dependency must ride along
                source = os.path.join(dlls_dir, dep)
            else:
                source = resolve(dep)
            found[low] = source
            if source is not None:
                worklist.append((source, True))
    _RUNTIME_SCAN_CACHE[base_dir] = found
    return found


def ensure_runtime_dlls(venv_dir):
    """Puts the distribution's misparked runtime DLLs inside the venv's
    ``Scripts`` directory, so the base interpreter's extension modules
    -- shared with the venv through ``pyvenv.cfg``'s home -- can load
    them once the runner ARMS that directory: ``dfnb_env_kernel`` adds
    the child's own exe directory for dependent-DLL loads at startup,
    because a base-tree pyd's altered search path starts at the pyd's
    own directory and never reaches the exe dir (round 17, probe-proven
    on sqlite3: the copied dll loads by absolute path without the arm,
    ``import sqlite3`` does not, and with the pair it imports as
    3.40.0). The set is SCAN-driven, not name-keyed (round 17's
    follow-up): :func:`_missing_runtime_dlls` discovers
    from the base tree's own import tables whatever this distribution
    parks outside directories a venv child can reach -- on this machine
    that heals ``sqlite3`` AND ``tkinter`` (tcl86t.dll lives only in
    Dragonfly's libs), and a future build that parks anything else
    heals the same way, no code change. The copy delivers INSIDE the
    venv precisely because the alternative -- adding Dragonfly's trees
    to the child's DLL search -- would expose the child to 718
    distribution DLLs and re-open the cross-tree hazard round 16
    closed. Idempotent per DLL and best-effort: a name already there
    stays untouched, and a copy that fails never breaks a kernel start.
    POSIX resolves its own shared objects and never runs this."""
    if os.name != 'nt':
        return
    scripts = os.path.dirname(venv_python(venv_dir))
    for name, source in sorted(_missing_runtime_dlls(
            _venv_base_prefix(venv_dir)).items()):
        if source is None:
            # best-effort delivery must never break a start, but a name
            # the scan resolved NOWHERE is the one breadcrumb that
            # explains a future mystery ImportError (round 17 review:
            # a silent skip recreates the original DLL load failure
            # exactly)
            _LOGGER.warning(
                'the runtime DLL %s (needed by the base interpreter\'s '
                'extension modules) was found in no runtime tree; some '
                'modules may fail to import in this environment', name)
            continue
        if os.path.isfile(os.path.join(scripts, name)):
            continue
        try:
            os.makedirs(scripts, exist_ok=True)
            shutil.copyfile(source, os.path.join(scripts, name))
        except OSError as exc:
            _LOGGER.warning('the runtime DLL %s could not be delivered '
                            '(%s); some modules may fail to import in '
                            'this environment', name, exc)


def popen(argv, cwd=None, stdin=None, merge_stderr=True):
    """The streaming runner for the environment children: a text-mode
    child with merged stdout/stderr (the dialogs show one interleaved
    log), devnull stdin, and CREATE_NO_WINDOW. Line-buffered so the
    reader thread below the dialog sees progress as it happens.

    ``stdin`` (a ``subprocess`` constant) defaults to DEVNULL; the
    environment KERNEL child passes PIPE because its control lines
    (run / stop / restart / quit / dfnb responses) arrive over stdin.
    ``merge_stderr=False`` (also the kernel child) keeps stderr on its
    own pipe: the kernel child's stdout is a pure JSON-line channel
    that a runner traceback must never corrupt, and the parent drains
    stderr separately for the crash report.

    The child env drops PYTHONPATH and QT_PLUGIN_PATH: a venv creation
    or pip install must see the BARE interpreter, never the ORS tree an
    ambient launcher process (the Dragonfly plugin process, the ORS
    verification launcher) carries in those variables (the ORS
    auto-import chain would bind its numpy inside the venv child and
    print the console banner over the transcript). The production app
    process already launches without them, so this only makes the
    in-launcher test runs identical to the real thing."""
    child_env = {key: value for key, value in os.environ.items()
                 if key not in ('PYTHONPATH', 'QT_PLUGIN_PATH')}
    return subprocess.Popen(
        argv, stdin=stdin if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        text=True, encoding='utf-8', errors='replace', bufsize=1,
        cwd=cwd, env=child_env, creationflags=CREATE_NO_WINDOW)
