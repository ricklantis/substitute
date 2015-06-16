# TODO: get rid of 'need'.  Use a function that memoizes the object instead.
import re, argparse, sys, os, string, shlex
from collections import OrderedDict, namedtuple
import curses.ascii

def indentify(s, indent='    '):
    return s.replace('\n', '\n' + indent)

def argv_to_shell(argv):
    quoteds = []
    for arg in argv:
        if re.match('^[a-zA-Z0-9_\.@/+=-]+$', arg):
            quoteds.append(arg)
        else:
            quoted = ''
            for c in arg:
                if c == '\n':
                    quoted += r'\n'
                elif c in r'$`\"':
                    quoted += '\\' + c
                elif not curses.ascii.isprint(c):
                    quoted += r'\x%02x' % ord(c)
                else:
                    quoted += c
            quoteds.append('"' + quoted + '"')
    return ' '.join(quoteds)

class DependencyNotFoundException(Exception):
    pass

# it must take no arguments, and throw DependencyNotFoundException on failure
class memoize(object):
    def __init__(self, f):
        self.f = f
    def __call__(self):
        if hasattr(self, 'threw'):
            raise self.threw
        elif hasattr(self, 'result'):
            return self.result
        else:
            try:
                self.result = self.f()
                return self.result
            except DependencyNotFoundException as self.threw:
                raise

class Pending(object):
    def __str__(self):
        return 'Pending',
    def resolve(self):
        return self.value
    # xxx py3
    def __getattr__(self, attr):
        return PendingAttribute(self, attr)

class PendingOption(Pending, namedtuple('PendingOption', 'opt')):
    def resolve(self):
        return self.opt.value
    def __repr__(self):
        return 'PendingOption(%s)' % (self.opt.name,)

class PendingAttribute(Pending, namedtuple('PendingAttribute', 'base attr')):
    def resolve(self):
        return getattr(self.base, self.attr)

class SettingsGroup(object):
    def __init__(self, group_parent=None, inherit_parent=None, name=None):
        object.__setattr__(self, 'group_parent', group_parent)
        object.__setattr__(self, 'inherit_parent', inherit_parent)
        object.__setattr__(self, 'vals', OrderedDict())
        if name is None:
            name = '<0x%x>' % (id(self),)
        object.__setattr__(self, 'name', name)
    @staticmethod
    def get_meat(self, attr, exctype=KeyError):
        allow_pending = not did_parse_args
        try:
            obj = object.__getattribute__(self, 'vals')[attr]
        except KeyError:
            inherit_parent = object.__getattribute__(self, 'inherit_parent')
            if inherit_parent is not None:
                ret = SettingsGroup.get_meat(inherit_parent, attr, exctype)
                if isinstance(ret, SettingsGroup):
                    ret = self[attr] = ret.new_inheritor(name='%s.%s' % (object.__getattribute__(self, 'name'), attr))
                return ret
            raise exctype(attr)
        else:
            if isinstance(obj, Pending):
                try:
                    return obj.resolve()
                except:
                    if not allow_pending:
                        raise Exception("setting %r is pending; you need to set it" % (attr,))
                    return obj
            return obj
    def __getattribute__(self, attr):
        try:
            return object.__getattribute__(self, attr)
        except AttributeError:
            return SettingsGroup.get_meat(self, attr, AttributeError)
    def __setattr__(self, attr, val):
        try:
            object.__getattribute__(self, attr)
        except:
            self[attr] = val
        else:
            object.__setattribute__(self, attr, val)
    def __getitem__(self, attr):
        return self.__getattribute__(attr)
    def __setitem__(self, attr, val):
        self.vals[attr] = val

    def __iter__(self):
        return self.vals.__iter__()
    def items(self):
        return self.vals.items()

    def __str__(self):
        s = 'SettingsGroup %s {\n' % (self.name,)
        o = self
        while True:
            for attr, val in o.vals.items():
                s += '    %s: %s\n' % (attr, indentify(str(val)))
            if o.inherit_parent is None:
                break
            o = o.inherit_parent
            s += '  [inherited from %s:]\n' % (o.name,)
        s += '}'
        return s

    def relative_lookup(self, name):
        name = re.sub('^\.\.', 'group_parent.', name)
        return eval('self.' + name)

    def add_setting_option(self, name, optname, optdesc, default, **kwargs):
        def f(value):
            self[name] = value
        default = Expansion(default, self) if isinstance(default, str) else default
        opt = Option(optname, optdesc, f, default, **kwargs)
        self[name] = PendingOption(opt)

    def new_inheritor(self, *args, **kwargs):
        return SettingsGroup(inherit_parent=self, *args, **kwargs)

    def new_child(self, name, *args, **kwargs):
        sg = SettingsGroup(group_parent=self, name='%s.%s' % (self.name, name), *args, **kwargs)
        self[name] = sg
        return sg

class OptSection(object):
    def __init__(self, desc):
        self.desc = desc
        self.opts = []
        all_opt_sections.append(self)
    def move_to_end(self):
        all_opt_sections.remove(self)
        all_opt_sections.append(self)

class Option(object):
    def __init__(self, name, help, on_set, default=None, bool=False, show=True, section=None, metavar=None, type=str, **kwargs):
        if name.startswith('--'):
            self.is_env = False
            assert set(kwargs).issubset({'nargs', 'choices', 'required', 'metavar'})
        elif name.endswith('='):
            self.is_env = True
            assert len(kwargs) == 0
            assert bool is False
        else:
            raise ValueError("name %r should be '--opt' or 'ENV='" % (name,))
        self.name = name
        self.help = help
        self.default = default
        self.on_set = on_set
        self.show = show
        self.type = type
        if metavar is None:
            metavar = '...'
        self.metavar = metavar
        self.bool = bool
        self.section = section if section is not None else default_opt_section
        self.section.opts.append(self)
        self.argparse_kw = kwargs.copy()
        all_options.append(self)
        if name in all_options_by_name:
            raise KeyError('trying to create Option with duplicate name %r; old is:\n%r' % (name, all_options_by_name[name]))
        all_options_by_name[name] = self
    def __repr__(self):
        value = repr(self.value) if hasattr(self, 'value') else '<none yet>'
        return 'Option(name=%r, help=%r, value=%s, default=%r)' % (self.name, self.help, value, self.default)

    def need(self):
        self.show = True

    def set(self, value):
        if value is None:
            value = self.default
            if callable(value): # Pending
                value = value()
        self.value = value
        if self.on_set is not None:
            self.on_set(value)

class Expansion(object):
    def __init__(self, fmt, base):
        assert isinstance(fmt, str)
        self.fmt = fmt
        self.deps = list(map(base.relative_lookup, re.findall('\((.*?)\)', fmt)))
    def __repr__(self):
        return 'Expansion(%r)' % (self.fmt,)
    def __call__(self):
        deps = self.deps[:]
        def get_dep(m):
            dep = deps.pop(0)
            if isinstance(dep, Pending):
                dep = dep.resolve()
        return re.sub('\((.*?)\)', get_dep, self.fmt)

def installation_dirs_group(sg):
    section = OptSection('Fine tuning of the installation directories:')
    for name, optname, optdesc, default in [
        ('prefix', '--prefix', '', '/usr/local'),
        ('exec_prefix', '--exec-prefix', '', '(prefix)'),
        ('bin', '--bindir', '', '(exec_prefix)/bin'),
        ('sbin', '--sbindir', '', '(exec_prefix)/sbin'),
        ('libexec', '--libexecdir', '', '(exec_prefix)/libexec'),
        ('etc', '--sysconfdir', '', '(prefix)/etc'),
        ('var', '--localstatedir', '', '(prefix)/var'),
        ('lib', '--libdir', '', '(prefix)/lib'),
        ('include', '--includedir', '', '(prefix)/include'),
        ('datarootdir', '--datarootdir', '', '(prefix)/share'),
        ('share', '--datadir', '', '(datarootdir)'),
        ('locale', '--localedir', '', '(datarootdir)/locale'),
        ('man', '--mandir', '', '(datarootdir)/man'),
        ('doc', '--docdir', '', '(datarootdir)/doc/(..package_unix_name)'),
        ('html', '--htmldir', '', '(doc)'),
        ('pdf', '--pdfdir', '', '(doc)'),
    ]:
        sg.add_setting_option(name, optname, optdesc, default, section=section, show=False)
    for ignored in ['--sharedstatedir', '--oldincludedir', '--infodir', '--dvidir', '--psdir']:
        Option(ignored, 'Ignored autotools compatibility setting', None, section=section, show=False)

def _make_argparse(include_unused, include_env):
    parser = argparse.ArgumentParser(
        add_help=False,
        usage='configure [OPTION]... [VAR=VALUE]...',
        prefix_chars=('-' + string.ascii_letters if include_env else '-'),
    )
    parser.add_argument('--help', action='store_true', help='Show this help', dest='__help')
    parser.add_argument('--help-all', action='store_true', help='Show this help, including unused options', dest='__help_all')
    for sect in all_opt_sections:
        def include(opt):
            return (include_unused or opt.show) and (include_env or not opt.is_env)
        if not any(map(include, sect.opts)):
            continue
        ag = parser.add_argument_group(description=sect.desc)
        for opt in sect.opts:
            if not include(opt):
                continue
            ag.add_argument(opt.name,
                            action='store_true' if opt.bool else 'store',
                            dest=opt.name[2:],
                            help=opt.help,
                            type=opt.type,
                            metavar=opt.metavar,
                            **opt.argparse_kw)
    return parser

def _print_help(include_unused=False):
    parser = _make_argparse(include_unused, include_env=True)
    parser.print_help()

def parse_args():
    default_opt_section.move_to_end()
    parser = _make_argparse(include_unused=True, include_env=False)
    args, argv = parser.parse_known_args()
    if args.__help or args.__help_all:
        _print_help(include_unused=args.__help_all)
        sys.exit(0)
    unrecognized_env = []
    def do_env_arg(arg):
        m = re.match('([^- ]+)=(.*)', arg)
        if not m:
            return True # keep for unrecognized
        if m.group(1) + '=' not in all_options_by_name:
            unrecognized_env.append(arg)
        else:
            os.environ[m.group(1)] = m.group(2)
        return False
    unrecognized_argv = list(filter(do_env_arg, argv))
    if unrecognized_argv:
        print ('unrecognized arguments: %s' % (argv_to_shell(unrecognized_argv),))
    if unrecognized_env:
        print ('unrecognized environment: %s' % (argv_to_shell(unrecognized_env),))
    if unrecognized_argv or unrecognized_env:
        _print_help()
        sys.exit(0)

    for opt in all_options:
        if opt.is_env:
            name = opt.name[:-1]
            opt.set(opt.type(os.environ[name]) if name in os.environ else None)
        else:
            opt.set(getattr(args, opt.name[2:]))
        #print args._unrecognized_args

    global did_parse_args
    did_parse_args = True

# -- toolchains --
class Triple(namedtuple('Triple', 'triple arch forgot1 os forgot2')):
    def __new__(self, triple):
        if isinstance(triple, Triple):
            return triple
        else:
            bits = triple.split('-')
            numbits = len(bits)
            if numbits > 4:
                raise Exception('strange triple %r' % (triple,))
            if numbits != 4:
                bits.insert(1, None)
            return super(Triple, self).__new__(self, triple, *((bits.pop(0) if bits else None) for i in range(4)))
    #def __repr__(self):
    def __str__(self):
        return self.triple

class Machine(object):
    def __init__(self, name, settings, triple_help, triple_default):
        self.name = name
        self.settings = settings
        def on_set(val):
            self.triple = val
        self.triple_option = Option('--' + name, help=triple_help, default=triple_default, on_set=on_set, type=Triple, section=triple_options_section)
        self.triple = PendingOption(self.triple_option)

    def __eq__(self, other):
        return self.triple == other.triple
    def __ne__(self, other):
        return self.triple != other.triple
    def __repr__(self):
        return 'Machine(name=%r, triple=%s)' % (self.name, repr(self.triple) if hasattr(self, 'triple') else '<none yet>')

    def is_cross(self):
        # This is only really meaningful in GNU land, as it decides whether to
        # prepend the triple (hopefully other targets are sane enough not to
        # have a special separate "cross compilation mode" that skips
        # configuration checks, but...).  Declared here because it may be
        # useful to override.
        if not hasattr(self, '_is_cross'):
            self._is_cross = self.triple != self.settings.build_machine().triple
        return self._is_cross

class UnixTool(object):
    def __init__(self, name, defaults, env, settings, toolchains, dont_suffix_env=False):
        self.name = name
        self.defaults = defaults
        self.env = env
        self.toolchains = toolchains
        self.needed = False
        self.settings = settings
        if toolchain.machine.name != 'host' and not dont_suffix_env:
            env = '%s_FOR_%s' % (env, toolchain.machine.name.upper())
        def on_set(val):
            if val is not None:
                self.opt_argv = shlex.split(val)
        self.argv_opt = Option(env + '=', help='Default: %r' % (defaults,), on_set=on_set, show=False)
        self.argv = memoize(self.argv)

    def __repr__(self):
        return 'UnixTool(name=%r, defaults=%r, env=%r)' % (self.name, self.defaults, self.env)

    def argv(self): # memoized
        # If the user specified it explicitly, don't question.
        if hasattr(self, 'opt_argv'):
            sys.stderr.write('Using %s from command line: %s\n' % (self.name, argv_to_shell(argv)))
            return self.opt_argv

        failure_notes = []
        for tc in self.toolchains:
            argv = tc.find_tool(self, failure_notes)
            if argv is not None:
                sys.stderr.write('Found %s: %s\n' % (self.name, argv_to_shell(argv)))
                return argv

        sys.stderr.write('** Failed to locate %s\n' % (self.name,))
        for fn in failure_notes:
            sys.stderr.write(fn)
        raise DependencyNotFoundException

    def locate_in_paths(self, prefix, paths):
        for path in paths:
            for default in self.defaults:
                default = prefix + default
                filename = os.path.join(path, default)
                if os.path.exists(filename):
                    return [filename]
        return None


class UnixToolchain(object):
    def __init__(self, machine, settings):
        self.machine = machine
        self.settings = settings

    def find_tool(self, tool, failure_notes):
        prefix = ''
        if self.machine.is_cross():
            prefix = self.toolchain.machine.triple.triple + '-'
            failure_notes.append('  note: detected cross compilation, so searched for %s-%s\n' % (self.machine.triple.triple, tool.name))
        return tool.locate_in_paths(prefix, self.settings.tool_search_paths)

    def get_tool_search_paths(self):
        return None # just use the default

class XcrunToolchain(object):
    def __init__(self, machine, settings):
        name = '--%sxcode-sdk' % (machine.name if machine.name != 'host' else '')
        self.sdk_opt = Option(name, help='Use Xcode SDK - `xcodebuild -showsdks` lists', on_set=self.on_set)
        self.got_sdk = False

    def on_set(self, val):
        if val is None:
            return
        # we don't need this value, but it might be helpful and serves as a check
        try:
            sdk_install_path = subprocess.check_output(['/usr/bin/xcrun', '--sdk', val, '--show-sdk-path'])
        except subprocess.CalledProcessError:
            sys.stderr.write('* Xcode SDK %r not found')
            return
        except OSError:
            sys.stderr.write('* Failed to run /usr/bin/xcrun')
            return
        sys.stderr.write('Xcode SDK path: %s\n' % (sdk_install_path,))
        self.got_sdk = True

    def find_tool(self, tool):
        if not self.got_sdk:
            return None
        argv = ['/usr/bin/xcrun', tool]
        p = subprocess.Popen(argv + ['--asdf'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        sod, sed = p.communicate()
        if p.returncode == 0 or not sed.startswith('xcrun: error: unable to find utility'):
            sys.stderr.write('* /usr/bin/xcrun')
            return None
        return argv

# Just a collection of common tools.
class CTools(object):
    def __init__(self, toolchains):
        tools = [
            # TODO figure out ld
            ('cc',   ['cc', 'gcc', 'clang'],    'CC'),
            ('cxx',  ['c++', 'g++', 'clang++'], 'CXX'),
            ('ar',),
            ('nm',),
            ('ranlib',),
            ('strip',),
            # GNU
            ('objdump', ['objdump', 'gobjdump'], 'OBJDUMP'),
            ('objcopy', ['objcopy', 'gobjcopy'], 'OBJCOPY'),
            # OS X
            ('lipo',),
        ]
        for spec in tools:
            if len(spec) == 1:
                name, defaults, env = spec[0], [spec[0]], spec[0].upper()
            else:
                name, defaults, env = spec
            tool = UnixTool(name, defaults, env, settings, toolchains=toolchains)
            setattr(self, name, tool)


# Get a list of appropriate toolchains for the specified machine.
def detect_toolchains(machine, settings):
    tcs = []
    if os.path.exists('/usr/bin/xcrun'):
        tcs.append(XcrunToolchain(machine, machine))
    tcs.append(UnixToolchain(machine, machine))
    return tcs

# this could be improved by having the lower entries regenerate from the upper.
# ... if this is actually useful
def make_machine_sg(settings, machine):
    sg = SettingsGroup(settings, machine.name=name)
    sg.machine = machine
    sg.toolchains = detect_toolchains(machine, settings)
    sg.c = CTools(sg.toolchains)

# A nicer - but optional - way of doing multiple tests that will print all the
# errors in one go and exit cleanly
def will_need(tests):
    failures = 0
    for test in tests:
        try:
            test()
        except DependencyNotFoundException:
            failures += 1
    if failures > 0:
        sys.stderr.write('(%d failures.)\n' % (failures,))
        sys.exit(1)

# -- init code --

did_parse_args = False

all_options = []
all_options_by_name = {}
all_opt_sections = []
default_opt_section = OptSection('Uncategorized options:')

settings_root = SettingsGroup(name='root')
settings_root.package_unix_name = Pending()
installation_dirs_group(settings_root.new_child('idirs'))

triple_options_section = OptSection('System types:')
settings_root.build = memoize(lambda: make_machine_sg(settings_root, Machine('build', 'the machine doing the build', '')))
settings_root.host = memoize(lambda: settings_root.build_machine() and make_machine_sg(settings_root, Machine('host', settings_root, 'the machine that will run the compiled program', lambda: settings_root.build_machine().triple)))
# ...'the machine that the program will itself compile programs for',

settings_root.tool_search_paths = os.environ['PATH'].split(':')

# --

