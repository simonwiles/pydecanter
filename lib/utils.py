import datetime
import hashlib
import os
import re

from html.parser import HTMLParser

##########################################################################
# classes and functions to support the js and css compression decorators.
##########################################################################


class PyDecanterParser(HTMLParser):
    def __init__(self, content, tags):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.content = content
        self.tags = tags
        self.elems = []
        self._current_tag = None
        self.feed(self.content)
        self.close()

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.tags:
            self.elems.append(
                {"tag": tag, "attrs": attrs, "attrs_dict": dict(attrs), "text": None}
            )
            self._current_tag = tag

    def handle_endtag(self, tag):
        if self._current_tag and self._current_tag == tag.lower():
            self._current_tag = None

    def handle_data(self, data):
        if self._current_tag in self.tags:
            self.elems[-1]["text"] = data


def get_file_hash(filename, length=None):
    filename = os.path.realpath(filename)
    hash_file = open(filename, "rb")
    try:
        content = hash_file.read()
    finally:
        hash_file.close()

    digest = hashlib.md5(content).hexdigest()
    if length:
        return digest[:length]
    return digest


def get_content_hash(content, length=None):
    digest = hashlib.md5(content.encode("utf8")).hexdigest()
    if length:
        return digest[:length]
    return digest


def extract_filename(url, base_url, base_root, referrer):
    """ get the appropriate filename from a url reference in the source """
    local_path = url

    # remove url fragment, if any
    local_path = local_path.rsplit("#", 1)[0]

    # remove querystring, if any
    local_path = local_path.rsplit("?", 1)[0]

    # remove the base_url prefix, if present
    if local_path.startswith(base_url):
        local_path = local_path.replace(base_url, "", 1)
    else:
        # FIXME: this was done in a hurry -- is it correct?
        local_path = os.path.join(os.path.dirname(referrer), local_path)

    # Re-build the local full path by adding root
    filename = os.path.join(base_root, local_path.lstrip("/"))
    return os.path.exists(filename) and filename


def add_cache_tag(url, base_url, base_root, referrer=""):
    filename = extract_filename(url, base_url, base_root, referrer)
    file_hash = None
    if filename:
        file_hash = get_file_hash(filename, 12)

    if file_hash is None:
        # Couldn't extract an accessible filepath --
        #   abort, and return the original URL unmodified
        return url

    # FIXME: why was I only modifying the url if it begins with '/'??
    # perhaps it was to prevent urls beginning with http(s) etc. from
    #  being parsed?  The above should take care of that, of course,
    #  and this would be better dealt with before this function is
    #  called, in any event
    # if url.startswith('/'):

    # TODO: do this with `from urllib.parse import urlparse`
    querystring = None
    fragment = None
    if "#" in url:
        url, fragment = url.rsplit("#", 1)
    if "?" in url:
        url, querystring = url.rsplit("?", 1)

    url, ext = url.rsplit(".", 1)
    url = ".".join((url, file_hash, ext))

    if querystring is not None:
        url = "?".join((url, querystring))
    if fragment is not None:
        url = "#".join((url, fragment))

    return url


def get_basename(url, base_url):
    if not url.startswith(base_url):
        raise Exception(
            "'%s' isn't accessible via COMPRESS_URL ('%s') and can't be "
            "compressed" % (url, base_url)
        )
    basename = url.replace(base_url, "", 1)
    # drop the querystring, which is used for non-compressed cache-busting.
    return basename.split("?", 1)[0]


def get_filename(basename, base_root):
    filename = os.path.join(base_root, basename.lstrip("/"))
    if os.path.exists(filename):
        return filename

    raise Exception("'%s' could not be found" % filename)


def get_cache_filepath(content, output_dir, ext):
    return os.path.join(output_dir, ".".join([get_content_hash(content, 12), ext]))


##########################################################
# standard utility functions made available to templates.
##########################################################
