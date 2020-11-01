#!/usr/bin/env python3

import atexit
import argparse
import errno
import logging
import os
import re
import shutil
import sys
from configparser import ConfigParser
from pathlib import Path
from wsgiref.handlers import SimpleHandler
from wsgiref.simple_server import WSGIServer, make_server

import bottle
import markdown
import slimit
import rcssmin
from mako.lookup import TemplateLookup
from bs4 import BeautifulSoup, Comment
from tidylib import tidy_document
from termcolor import colored
from socketserver import ThreadingMixIn

from lib.monitor import Monitor

from lib.utils import add_cache_tag, get_file_hash


DEFAULT_ARGS = {
    "host": "127.0.0.1",
    "port": 8080,
    "base_url": "/",
    "base_root": "",
    "assets_dir": "assets",
    "verbose": True,
    "debug": True,
    "compress": False,
    "context": {},
}


# list of extensions that need to be hard-coded to specific mime-types
#  (these are ones which are not properly detected by bottle).
FORCE_MIMETYPES = ((".vtt", "text/vtt"),)


TIDY_OPTIONS = {
    "doctype": "html5",
    # 'hide-comments': True,  # no good; strips IE conditional comments too :(
    "tidy-mark": False,
    "indent": True,
    "vertical-space": False,
    "output-xhtml": False,
    "wrap": 0,
    "wrap-attributes": False,
    "break-before-br": True,
    "punctuation-wrap": True,
    "drop-empty-elements": False,
    "new-blocklevel-tags": "script, svg, path",
}


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    pass


class PyDecanter(object):

    RE_CACHEBUSTABLE = re.compile(r"(.*)\.(jpe?g|gif|png|svg|ttf|woff|woff2)")
    RE_CACHEBUSTER = re.compile(
        r"(.*)\.[0-9a-f]{12}\.(jpe?g|gif|png|svg|ttf|woff|woff2)"
    )
    CACHE_TAG_EXCS = (
        "favicon",
        "apple-touch-icon",
        "android-chrome",
        "mstile",
        "safari-pinned-tab",
    )

    # A tuple of regular expressions representing files which should not be
    #  output as part of the build process.
    PRIVATE_FILES = (
        re.compile(r".*\.mako$"),
        re.compile(r"(^|.*\/)\.(?!htaccess)"),
        re.compile(r"^\.build"),
        re.compile(r"^\.git"),
        re.compile(r"^\.sass-cache"),
    )

    # A tuple of extensions for files that should be processed by Mako
    TEMPLATE_EXTS = ("html",)

    def __init__(self, args):

        # the Bottle application
        self.app = bottle.Bottle()

        # the base root from which to serve files
        #  (mako needs a string, not a Path() object).
        self.base_root = str(args.base_root.resolve())
        self.base_url = re.sub(r"/+", "/", "/{0}/".format(args.base_url))

        self.assets_dir = args.assets_dir

        if "private" in args:
            self.PRIVATE_FILES = self.PRIVATE_FILES + tuple(
                re.compile(_) for _ in args.private.split(",")
            )

        # the TemplateLookup of Mako
        self.templates = TemplateLookup(
            directories=[self.base_root],
            imports=[
                "from markdown import markdown",
                "from lib.typogrify import amp, caps, initial_quotes,"
                "    smartypants, titlecase, typogrify, widont",
                "from lib.decorators import css, js",
            ],
            input_encoding="UTF8",
            collection_size=100,
            default_filters=["trim"],
        )

        self.context = {
            "base_url": self.base_url.rstrip("/"),
            "base_root": Path(self.base_root),
            "assets_dir": self.assets_dir,
            "output_root": self.base_root,
            "compress": False,
        }

        # establish routing config for bottle
        self.app.route(
            "{0}<path:path>".format(self.base_url), method="GET", callback=self.render
        )
        self.app.route(
            self.base_url, method="GET", callback=lambda: self.render("index.html")
        )

        # if there's a 404.html file in the root folder, render this for
        #  404 responses (this returns the wrong response code, of course,
        #  but there's no point in modifying this on a dev-only server).
        if os.path.isfile(os.path.join(self.base_root, "404.html")):
            self.app.error(404)(lambda err: self.render("404.html"))

        # prep the simple WSGI server (could use waitress here, but the extra
        #  dependency is not really necessary for dev-only purposes).
        self.server = make_server(args.host, args.port, self, ThreadingWSGIServer)

        def restart(modified_path, base_dir=os.getcwd()):
            """automatically restart the server if changes are made to python
            files on this path."""
            self.server.server_close()
            self.server.shutdown()
            logging.info("change detected to '%s' -- restarting server", modified_path)
            args = sys.argv[:]
            args.insert(0, sys.executable)
            os.chdir(base_dir)
            os.execv(sys.executable, args)

        monitor = Monitor(interval=1.0)
        if args.ini_file is not None:
            monitor.track(str(Path(os.path.expanduser(args.ini_file)).resolve()))
        monitor.on_modified = restart

    def is_private(self, path):
        """returns True if the path represents a file which should not be
        output as part of the static site, and False otherwise."""
        for regex in self.PRIVATE_FILES:
            if regex.match(path):
                return True
        return False

    def render(self, path):
        if os.path.isdir(os.path.join(self.base_root, path)):
            path = os.path.join(path, "index.html")

        if self.is_private(path) and not path.startswith(self.assets_dir):
            return bottle.HTTPError(
                403, "Direct access to {0} is forbidden".format(path)
            )

        if path.split(".")[-1] in self.TEMPLATE_EXTS and self.templates.has_template(
            path
        ):

            template = self.templates.get_template(path)
            self.context.update({"path": path})
            document = template.render_unicode(**self.context)

            if self.context["compress"]:
                document = self.add_image_cache_tags(document)
                document = self.tidy_html(document)

            return document.encode("utf-8")

        # if using the static filters whilst using the dev. server, we need to
        #  remove the hash from image assets (note that the build_static
        #  function will generate image assets which actually _do_ have these
        #  hashes in the filename, so there is no need for a similar rewrite
        #  in the apache2/nginx config).
        if self.RE_CACHEBUSTER.match(path):
            path = self.RE_CACHEBUSTER.sub(r"\1.\2", path)

        # bottle does not detext some mimetypes correctly, so here we check for
        #  any that need to be hard-coded.
        for ext, mimetype in FORCE_MIMETYPES:
            if path.endswith(ext):
                return bottle.static_file(path, root=self.base_root, mimetype=mimetype)

        return bottle.static_file(path, root=self.base_root)

    @staticmethod
    def tidy_html(document):

        # pre-process to remove comments (htmltidy's option to do so will
        #  also strip IE conditional comments).
        soup = BeautifulSoup(document, "lxml")
        for cmt in soup.findAll(text=lambda text: isinstance(text, Comment)):
            if not (cmt.startswith("[if") or cmt.startswith("<![endif")):
                cmt.extract()

        document, errors_ = tidy_document(str(soup), options=TIDY_OPTIONS)

        # clean-up some line-breaks associated with conditional comments
        document = re.sub(r"(?<!\s)<!--", "\n<!--", document)

        # adjust some of the htmltidy indenting to my own taste :)
        document = re.sub(
            r"(?m)^(\s+)(.*?)(<script[^>]*>)\n</script>",
            r"\1\2\n\1\3</script>",
            document,
        )
        document = re.sub(
            r"(?m)^(\s+)(.*?)(<script[^>]*>)\n((?:[^\n]+\n)+)\s+</script>",
            r"\1\2\n\1\3\n\1\4\1</script>",
            document,
        )
        re_single_line_elems = re.compile(
            r"^(\s*<([a-z][a-z0-9]*)\b[^>]*>)\s*([^\n]*?)\s*</\2>",
            re.DOTALL | re.MULTILINE,
        )
        document = re_single_line_elems.sub(r"\1\3</\2>", document)
        return document

    def add_image_cache_tags(self, document):

        re_svgfallback = re.compile(
            r"this.onerror=null;\s*this.src='([^\']+\.(?:png|gif|jpe?g))'"
        )

        soup = BeautifulSoup(document, "lxml")
        for img in soup.findAll("img"):
            if not any([exc in img["src"] for exc in self.CACHE_TAG_EXCS]):
                img["src"] = add_cache_tag(
                    img["src"], self.base_url, self.base_root, self.context["path"]
                )
            # also need to deal with attributes of the form:
            #   onerror="this.onerror=null; this.src='<fallback_image>'"
            if "onerror" in img.attrs and re_svgfallback.match(img["onerror"]):
                img["onerror"] = re_svgfallback.sub(
                    lambda m: m.group(0).replace(
                        m.group(1),
                        add_cache_tag(m.group(1), self.base_url, self.base_root),
                    ),
                    img["onerror"],
                )

        # deal with <a href="<cache-tagged image here>">
        re_local_image = re.compile("^" + self.base_url + r"[^/].+\.(?:png|gif|jpe?g)$")
        for anchor in soup.findAll("a", href=re_local_image):
            if not any([exc in anchor["href"] for exc in self.CACHE_TAG_EXCS]):
                anchor["href"] = add_cache_tag(
                    anchor["href"], self.base_url, self.base_root, self.context["path"]
                )

        return str(soup)

    @staticmethod
    def create_cache_dir(cache_dir, cleanup=True):
        """create a cache folder on the base_root path, and automatically
        remove it again when the program exits (unless it was already
        there to begin with).
        """

        if not os.path.isdir(cache_dir):
            try:
                os.makedirs(cache_dir)
            except OSError as exc:  # Python >2.5
                if exc.errno == errno.EEXIST:
                    pass
                else:
                    raise

        def cleanup_func():
            """ Helper function to remove the temporary cache folder. """
            shutil.rmtree(cache_dir)

        if cleanup:
            atexit.register(cleanup_func)

    def run(self, args):
        """ Launch a development web server. """

        # apply any context passed in from a config file
        self.context.update(args.context)

        # if we're running with `static = True`, but running a dev. server,
        #  we need a (temporary) cache folder to be available.
        if args.compress:
            self.context.update({"compress": True})
            self.assets_dir = os.path.join(self.base_root, self.assets_dir)
            self.create_cache_dir(self.assets_dir)

        try:
            logging.info(
                "server running at http://%s%s",
                "{}:{}".format(*self.server.server_address),
                self.base_url,
            )
            self.server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            logging.info("Quitting Server!")
            self.server.server_close()
            self.server.shutdown()
            raise SystemExit

    def get(self, path):
        """ get the content of a url as rendered by bottle """
        handler = SimpleHandler(sys.stdin, sys.stdout, sys.stderr, {})
        handler.setup_environ()
        env = handler.environ
        env.update(
            {
                "PATH_INFO": "{0}/{1}".format(self.base_url, path),
                "REQUEST_METHOD": "GET",
            }
        )
        out = b"".join(self.app(env, lambda *args: None))
        return out

    def build_static(self, args):
        """
        Generates a complete static version of the web site. It will stored in
        output_folder.
        """

        # apply any context passed in from a config file
        self.context.update(args.context)

        self.assets_dir = os.path.join(args.output_dir, self.assets_dir)
        self.create_cache_dir(self.assets_dir, False)
        self.context.update({"output_root": args.output_dir, "compress": args.compress})
        public_files = []
        for dirpath, dirnames_, filenames in os.walk(self.base_root):
            for filename in filenames:
                path = os.path.relpath(os.path.join(dirpath, filename), self.base_root)
                if not self.is_private(path):
                    public_files.append(path)

        for filepath in public_files:

            # if filepath represents a cacheable asset, add a (hash) tag
            #  to the output filename.
            if (
                self.context["compress"]
                and self.RE_CACHEBUSTABLE.match(filepath)
                and not any(exc in filepath for exc in self.CACHE_TAG_EXCS)
            ):

                filehash = get_file_hash(os.path.join(self.base_root, filepath), 12)
                output_path = self.RE_CACHEBUSTABLE.sub(
                    lambda m, fh=filehash: ".".join((m.group(1), fh, m.group(2))),
                    filepath,
                )

                output_path = os.path.join(args.output_dir, output_path)

            else:
                output_path = os.path.join(args.output_dir, filepath)

            dirname = os.path.dirname(output_path)
            if not os.path.exists(dirname):
                os.makedirs(dirname)

            if filepath.endswith((".html", ".css", ".js")):
                logging.info(colored("generating %s", "blue"), filepath)
                content = self.get(filepath)

                with open(output_path, "wb") as _fh:
                    _fh.write(content)

            else:
                # just copy the file instead
                logging.info(colored("copying %s", "green"), filepath)
                shutil.copy2(os.path.join(self.base_root, filepath), output_path)

    def __call__(self, environ, start_response):
        return self.app(environ, start_response)


def get_config_from_ini_file(args):

    cfg = ConfigParser()
    cfg.optionxform = str  # prevent automatic lowercasing of all keys
    cfg.read(args.ini_file)

    args_dict = vars(args)

    if args.command:
        ini_args = cfg[args.command]

    elif "serve" in cfg.sections():
        args.command = "serve"
        ini_args = cfg["serve"]

    elif "build" in cfg.sections():
        args.command = "build"
        ini_args = cfg["build"]

    ini_file_location = Path(os.path.expanduser(args.ini_file)).parent
    if args_dict.get("base_root", None) is None and "base_root" in ini_args:
        args_dict["base_root"] = ini_file_location / os.path.expanduser(
            ini_args["base_root"]
        )
    else:
        args_dict["base_root"] = ini_file_location

    for arg in ["base_url", "host", "private"]:
        if args_dict.get(arg, None) is None and arg in ini_args:
            args_dict[arg] = ini_args[arg]

    for arg in ["verbose", "debug", "compress"]:
        if args_dict.get(arg, None) is None and arg in ini_args:
            args_dict[arg] = ini_args.getboolean(arg)

    for arg in ["port"]:
        if args_dict.get(arg, None) is None and arg in ini_args:
            args_dict[arg] = ini_args.getint(arg)

    if "context" in cfg:
        args_dict["context"] = dict(cfg["context"])

    return args


def main():
    """started from command-line
    The idea here is that arguments can be supplied on the command-line or
    in a .ini file (the former taking precedence), and in case neither is
    provided, defaults are used.
    """

    parser = argparse.ArgumentParser(
        description="PyDecanter, static web site generator"
    )

    parser.add_argument(
        "-i",
        "--ini-file",
        action="store",
        default=None,
        help="use a .ini configuration file",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=None,
        help="increase logging verbosity",
    )

    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        default=None,
        help="print debug information",
    )

    parser.add_argument(
        "-p", "--port", type=int, default=None, help="port to bind the WSGI server to"
    )

    parser.add_argument(
        "-H", "--host", default=None, help="host to bind the WSGI server to"
    )

    parser.add_argument(
        "-f", "--base-root", default=None, help="root dir to serve files from"
    )

    parser.add_argument(
        "-r",
        "--base-url",
        default=None,
        help="specify a base URL to serve from (e.g. /subfolder)",
    )

    parser.add_argument(
        "-c",
        "--compress",
        action="store_true",
        default=None,
        help='compress blocks decorated "css" or "js" decorators ('
        "note: default for `serve` is False, for `build` is True)",
    )

    parser.add_argument("command", nargs="?", help="[serve|build]")

    parser.add_argument(
        "-o", "--output-dir", nargs="?", help="folder to store the files"
    )

    args = parser.parse_args()

    if args.command and args.command not in ("serve", "build"):
        parser.print_help()
        raise SystemExit

    if args.ini_file and Path(args.ini_file).is_file():
        args = get_config_from_ini_file(args)
    elif args.base_root:
        args.base_root = Path(args.base_root)
    else:
        raise SystemExit(1)

    args_dict = vars(args)
    args_dict.update(
        {
            k: v
            for k, v in DEFAULT_ARGS.items()
            if k not in args_dict or args_dict[k] is None
        }
    )

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(message)s")

    if args.command == "build" and args.output_dir is None:
        parser.print_help()
        raise SystemExit

    bottle.debug(args.debug)
    decanter = PyDecanter(args)

    print(colored(parser.description, "green", attrs=["bold"]))
    print(colored("----", "green", attrs=["bold"]))

    if args.command == "serve":
        decanter.run(args)

    if args.command == "build":
        decanter.build_static(args)


if __name__ == "__main__":
    main()
