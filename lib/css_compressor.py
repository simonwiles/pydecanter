import codecs
import re
import os
from posixpath import normpath

from .utils import PyDecanterParser, \
    get_cache_filepath, get_basename, get_filename, add_cache_tag
from rcssmin import cssmin


class CssAbsoluteFilter(object):

    RE_URL = re.compile(r'url\(([^\)]+)\)')
    RE_SRC = re.compile(r'src=([\'"])(.+?)\1')

    def __init__(self, base_root, base_url):

        self.base_root = base_root
        self.base_url = base_url.rstrip('/')
        self.directory_name = None

    def process(self, content, filename=None, basename=None):

        if filename is None:
            return content

        filename = os.path.normcase(os.path.abspath(filename))

        path = basename.lstrip('/')
        self.directory_name = '/'.join((self.base_url, os.path.dirname(path)))

        return self.RE_SRC.sub(
            self.src_converter, self.RE_URL.sub(self.url_converter, content))

    def _converter(self, matchobj, group, template):
        url = matchobj.group(group)
        url = url.strip(' \'"')
        if url.startswith('#'):
            return "url('{0}')".format(url)
        elif url.startswith('data:'):
            return "url('{0}')".format(url)
        elif url.startswith('/'):
            return "url('{0}')".format(
                add_cache_tag(url, self.base_url, self.base_root))
        full_url = normpath('/'.join([str(self.directory_name), url]))
        return template.format(
            add_cache_tag(full_url, self.base_url, self.base_root))

    def url_converter(self, matchobj):
        return self._converter(matchobj, 1, "url('{0}')")

    def src_converter(self, matchobj):
        return self._converter(matchobj, 2, "src='{0}'")


def compress_css(css_block, context):

    # ensure our base_url ends in a (single) forward stroke
    base_url = re.sub(r'//', '/', '/' + context['base_url'].strip('/') + '/')
    base_root = context['base_root']
    output_root = context['output_root']
    assets_dir = os.path.join(output_root, context['assets_dir'])

    parser = PyDecanterParser(css_block, ['style', 'link'])

    # we need to group the stylesheets (in order) according to the media type
    # (we might have two for screen, one for print, then another for screen,
    #  and so on, and the order matters).
    media_nodes = []
    node = []
    for elem in parser.elems:
        data = None
        if elem['tag'] == 'link' and \
                elem['attrs_dict']['rel'].lower() == 'stylesheet':

            basename = get_basename(elem['attrs_dict']['href'], base_url)
            filename = get_filename(basename, context['base_root'])
            data = ('file', filename, basename, elem)
        elif elem['tag'] == 'style':
            data = ('inline', elem['text'], None, elem)

        if data:
            node.append(data)
            media = elem['attrs_dict'].get('media', None)
            # Append to the previous node if it had the same media type
            if media_nodes and media_nodes[-1][0] == media:
                media_nodes[-1][1].append(data)
            else:
                media_nodes.append((media, [data]))

    # now for each block of contiguous stylesheets with the same media type,
    #  we grab the contents, run it through the CssAbsoluteFilter (this, of
    #  course, has to be done per file, since the assets referenced by each
    #  stylesheet will probably be using relative urls).
    css_abs_filter = CssAbsoluteFilter(base_root, base_url)
    output_elems = []
    for mtype, node in media_nodes:
        content = []
        for hunk_type, value, basename, elem in node:
            if hunk_type == 'file':
                with codecs.open(value, 'rb', 'utf8') as filehandle:
                    value = filehandle.read()

            content.append(css_abs_filter.process(value, basename, basename))

        # now join the content and run it through cssmin
        content = cssmin(''.join(content))

        # create the output file, write the compressed content, and add it to
        #  the list for outputting.
        outputpath = get_cache_filepath(content, assets_dir, 'css')
        if not os.path.exists(outputpath):
            with codecs.open(outputpath, 'w', 'utf8') as file_handle:
                file_handle.write(content)

        url = re.sub(r'//', '/', outputpath.replace(output_root, base_url))
        output_elems.append((url, mtype))

    return '\n'.join([
        '<link rel="stylesheet" href="{0}" {1}/>'.format(
        url, 'media="{0}" '.format(media) if media is not None else '')
        for url, media in output_elems
    ])
