#!/usr/bin/env python
"""Test that autopep8 runs without crashing on various Python files."""

import ast
import contextlib
import difflib
import dis
import os
import re
import sys
import subprocess
import tempfile
import tokenize

try:
    from StringIO import StringIO
except ImportError:
    from io import StringIO


if sys.stdout.isatty():
    YELLOW = '\033[33m'
    END = '\033[0m'
else:
    YELLOW = ''
    END = ''


def colored(text, color):
    """Return color coded text."""
    return color + text + END


def run(filename, fast_check=False, passes=2000,
        ignore='', check_ignore='', verbose=False,
        comparison_function=None,
        aggressive=False):
    """Run autopep8 on file at filename.

    Return True on success.

    """
    autopep8_path = os.path.split(os.path.abspath(
        os.path.dirname(__file__)))[0]
    autoppe8_bin = os.path.join(autopep8_path, 'autopep8.py')
    command = ([autoppe8_bin] + (['--verbose'] if verbose else []) +
               ['--pep8-passes={p}'.format(p=passes),
                '--ignore=' + ignore, filename] +
               (['--aggressive'] if aggressive else []))

    if fast_check:
        if 0 != subprocess.call(command + ['--diff']):
            sys.stderr.write('autopep8 crashed on ' + filename + '\n')
            return False
    else:
        with tempfile.NamedTemporaryFile(suffix='.py') as tmp_file:
            if 0 != subprocess.call(command, stdout=tmp_file):
                sys.stderr.write('autopep8 crashed on ' + filename + '\n')
                return False

            if 0 != subprocess.call(
                ['pep8',
                 '--ignore=' + ','.join([x for x in ignore.split(',') +
                                         check_ignore.split(',') if x]),
                 '--show-source', tmp_file.name],
                    stdout=sys.stdout):
                sys.stderr.write('autopep8 did not completely fix ' +
                                 filename + '\n')

            try:
                if check_syntax(filename):
                    try:
                        check_syntax(tmp_file.name, raise_error=True)
                    except (SyntaxError, TypeError,
                            UnicodeDecodeError) as exception:
                        sys.stderr.write('autopep8 broke ' + filename + '\n' +
                                         str(exception) + '\n')
                        return False

                    if comparison_function:
                        if not comparison_function(filename, tmp_file.name):
                            return False
            except IOError as exception:
                sys.stderr.write(str(exception) + '\n')

    return True


def _detect_encoding(filename):
    """Return file encoding."""
    try:
        # Python 3
        try:
            with open(filename, 'rb') as input_file:
                encoding = tokenize.detect_encoding(input_file.readline)[0]

                # Check for correctness of encoding
                import io
                with io.TextIOWrapper(input_file, encoding) as wrapper:
                    wrapper.read()

            return encoding
        except (SyntaxError, LookupError, UnicodeDecodeError):
            return 'latin-1'
    except AttributeError:
        return 'utf-8'


def open_with_encoding(filename, encoding, mode='r'):
    """Open file with a specific encoding."""
    try:
        # Python 3
        return open(filename, mode=mode, encoding=encoding)
    except TypeError:
        return open(filename, mode=mode)


def check_syntax(filename, raise_error=False):
    """Return True if syntax is okay."""
    with open_with_encoding(
            filename, _detect_encoding(filename)) as input_file:
        try:
            compile(input_file.read(), '<string>', 'exec')
            return True
        except (SyntaxError, TypeError, UnicodeDecodeError):
            if raise_error:
                raise
            else:
                return False


def compare_ast(old_filename, new_filename):
    """Return True if AST of the two files are equivalent."""
    if ast_dump(old_filename) != ast_dump(new_filename):
        sys.stderr.write(
            'New AST does not match original ' +
            old_filename + '\n')
        return False
    return True


def ast_dump(filename):
    with open(filename) as f:
        return ast.dump(ast.parse(f.read(), '<string>', 'exec'))


def compare_bytecode(old_filename, new_filename):
    """Return True if bytecode of the two files are equivalent."""
    before_bytecode = disassemble(old_filename)
    after_bytecode = disassemble(new_filename)
    if before_bytecode != after_bytecode:
        sys.stderr.write(
            'New bytecode does not match original ' +
            old_filename + '\n' +
            ''.join(difflib.unified_diff(
                before_bytecode.splitlines(True),
                after_bytecode.splitlines(True))) + '\n')
        return False
    return True


def disassemble(filename):
    """dis, but without line numbers."""
    with open_with_encoding(filename, _detect_encoding(filename)) as f:
        code = compile(f.read(), '<string>', 'exec')

    return filter_disassembly('\n'.join(_disassemble(code)))


def is_bytecode_string(text):
    """Return True if this is a bytecode string."""
    assert text.startswith('(')
    text = text[1:]
    for prefix in ['ur', 'br', 'u', 'b', 'r']:  # Longer one first.
        if text.startswith(prefix):
            text = text[len(prefix):]
            break

    for symbol in ['"', "'"]:
        if text.startswith(symbol):
            return True
    return False


def filter_disassembly(text):
    """Filter out innocuous differences."""
    # Ignore formatting of docstrings. We modify docstrings for indentation and
    # trailing whitespace.
    lines = text.splitlines()
    for index, current_line in enumerate(lines):
        tokens = current_line.split()
        if len(tokens) <= 3:
            continue

        if tokens[1] == 'STORE_NAME' and tokens[3] == '(__doc__)':
            fixed = re.sub(r'\s', '', lines[index - 1])
            lines[index - 1] = fixed.replace(
                r'\n', '').replace(r'\r', '').replace(r'\t', '')

        # BUILD_TUPLE and LOAD_CONST are sometimes used interchangeably.
        if tokens[1] == 'LOAD_CONST' and tokens[3] == '(())':
            lines[index] = lines[index].replace(
                'LOAD_CONST               8 (())',
                'BUILD_TUPLE              0')

        # Ignore trailing whitespace in multi-line strings.
        if tokens[1] == 'LOAD_CONST' and is_bytecode_string(tokens[3]):
            # Note that we are not matching actual newlines, but escaped
            # newlines within a string.
            lines[index] = re.sub(r'\s+\\n', r'\\n', lines[index])

        # LOAD_NAME and LOAD_CONST are sometimes used interchangeably.
        if tokens[1] == 'LOAD_NAME':
            if tokens[3] == '(False)':
                lines[index] = lines[index].replace(
                    'LOAD_NAME               21 (False)',
                    'LOAD_CONST              12 (False)')
            elif tokens[3] == '(None)':
                lines[index] = lines[index].replace(
                    'LOAD_NAME               14 (None)',
                    'LOAD_CONST               5 (None)')

    return '\n'.join(lines)


def _disassemble(code):
    """Disassemble a code object."""
    sio = StringIO()

    findlinestarts = dis.findlinestarts
    dis.findlinestarts = lambda _: {}
    sys.stdout, sio = sio, sys.stdout
    try:
        dis.dis(code)
    finally:
        sys.stdout, sio = sio, sys.stdout
        dis.findlinestarts = findlinestarts

    disassembled_code = [
        re.sub('<code object .* line [0-9]+>',
               '<code object>',  sio.getvalue())]

    for c in code.co_consts:
        if hasattr(c, 'co_code'):
            disassembled_code += _disassemble(c)

    return disassembled_code


def process_args():
    """Return processed arguments (options and positional arguments)."""
    import optparse
    parser = optparse.OptionParser()
    parser.add_option('--fast-check', action='store_true',
                      help='ignore incomplete PEP8 fixes and broken files')
    parser.add_option('--ignore',
                      help='comma-separated errors to ignore',
                      default='')
    parser.add_option('--check-ignore',
                      help='comma-separated errors to ignore when checking '
                           'for completeness',
                      default='')
    parser.add_option('-p', '--pep8-passes',
                      help='maximum number of additional pep8 passes'
                           ' (default: %default)',
                      default=2000)
    parser.add_option('--compare-bytecode', action='store_true',
                      help='compare bytecode before and after fixes; '
                           'should be used with '
                           '--ignore=E711,E721,W601,W602,W604')
    parser.add_option('--compare-ast', action='store_true',
                      help='compare AST before and after fixes; '
                           'should be used with '
                           '--ignore=E711,E721,W601,W602,W604')
    parser.add_option('--aggressive', action='store_true',
                      help='run autopep8 in aggressive mode')

    parser.add_option(
        '--timeout',
        help='stop testing additional files after this amount of time '
             '(default: %default)',
        default=-1,
        type=float)

    parser.add_option('-v', '--verbose', action='store_true',
                      help='print verbose messages')

    return parser.parse_args()


class TimeoutException(Exception):

    """Timeout exception."""


def timeout(_, __):
    raise TimeoutException()


def check(opts, args):
    """Run recursively run autopep8 on directory of files.

    Return False if the fix results in broken syntax.

    """
    if args:
        dir_paths = args
    else:
        dir_paths = sys.path

    filenames = dir_paths
    completed_filenames = set()

    if opts.compare_bytecode and opts.compare_ast:
        comparison_function = lambda x, y: (compare_ast(x, y) or
                                            compare_bytecode(x, y))
    elif opts.compare_bytecode:
        comparison_function = compare_bytecode
    elif opts.compare_ast:
        comparison_function = compare_ast
    else:
        comparison_function = None

    try:
        import signal
        if opts.timeout > 0:
            signal.signal(signal.SIGALRM, timeout)
            signal.alarm(int(opts.timeout))

        while filenames:
            name = os.path.realpath(filenames.pop(0))
            if name in completed_filenames:
                sys.stderr.write(
                    colored('--->  Skipping previously tested ' + name + '\n',
                            YELLOW))
                continue
            else:
                completed_filenames.update(name)

            try:
                is_directory = os.path.isdir(name)
            except UnicodeEncodeError:
                continue

            if is_directory:
                for root, directories, children in os.walk(name):
                    filenames += [os.path.join(root, f) for f in children
                                  if f.endswith('.py') and
                                  not f.startswith('.')]
                    for d in directories:
                        if d.startswith('.'):
                            directories.remove(d)
            else:
                verbose_message = '--->  Testing with '
                try:
                    verbose_message += name
                except UnicodeEncodeError:
                    verbose_message += '...'
                sys.stderr.write(colored(verbose_message + '\n', YELLOW))

                if not run(os.path.join(name),
                           fast_check=opts.fast_check,
                           passes=opts.pep8_passes,
                           ignore=opts.ignore,
                           check_ignore=opts.check_ignore,
                           verbose=opts.verbose,
                           comparison_function=comparison_function,
                           aggressive=opts.aggressive):
                    return False
    except TimeoutException:
        sys.stderr.write('Timed out\n')
    finally:
        if opts.timeout > 0:
            signal.alarm(0)

    return True


def main():
    """Run main."""
    return 0 if check(*process_args()) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
