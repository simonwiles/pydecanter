import codecs
import re
import os
from posixpath import normpath

from slimit import minify

from .utils import PyDecanterParser, \
    get_cache_filepath, get_basename, get_filename, add_cache_tag

# monkeypatch to solve problem with ply=3.4
#  (latest ply=3.6 solves this problem, but creates more)
from ply import yacc
def __getitem__(self,n):
    if isinstance(n, slice):
        return self.__getslice__(n.start, n.stop)
    if n >= 0: return self.slice[n].value
    else: return self.stack[n].value

yacc.YaccProduction.__getitem__ = __getitem__



def do_replacements(text):

    text = re.sub(r'[\'"]?use strict[\'"]?;?', '', text)

    return text


def compress_js(js_block, context):

    # ensure our base_url ends in a (single) forward stroke
    base_url = context['base_url'].rstrip('/') + '/'
    base_root = context['base_root']
    output_root = context['output_root']
    assets_dir = os.path.join(output_root, context['assets_dir'])

    parser = PyDecanterParser(js_block, ['script'])

    nodes = []
    for elem in parser.elems:
        if 'src' in elem['attrs_dict']:
            basename = get_basename(elem['attrs_dict']['src'], base_url)
            filename = get_filename(basename, context['base_root'])
            nodes.append(('file', filename, basename, elem))
        else:
            nodes.append(('inline', elem['text'], None, elem))

    content = []
    for hunk_type, value, basename, elem in nodes:
        if hunk_type == 'file':
            with open(value) as _fh:
                value = _fh.read()

        value = do_replacements(value)
        content.append(value)

    content = ''.join(content)

    content = minify(content, mangle=True, mangle_toplevel=False)

    # APPLY FILTERS

    # create the output file, write the compressed content, and return the
    #  prepared tag

    outputpath = get_cache_filepath(content, assets_dir, 'js')
    if not os.path.exists(outputpath):
        with codecs.open(outputpath, 'w', 'utf8') as file_handle:
            file_handle.write(content)

    url = re.sub(r'//', '/', outputpath.replace(output_root, base_url))

    return '<script src="{0}"></script>'.format(url)
