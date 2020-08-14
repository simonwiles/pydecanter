import traceback
from mako.runtime import capture

from .js_compressor import compress_js
from .css_compressor import compress_css


def css(fn):

    def decorate(context, *args, **kwargs):
        css_block = capture(context, fn, *args, **kwargs).strip()
        if context['compress']:
            try:
                css_block = compress_css(css_block, context)
            except Exception as err_:
                print(traceback.format_exc())
                raise
        context.write(css_block)
        return

    return decorate


def js(fn):

    def decorate(context, *args, **kwargs):
        js_block = capture(context, fn, *args, **kwargs).strip()
        if context['compress']:
            try:
                js_block = compress_js(js_block, context)
            except Exception as err_:
                print(traceback.format_exc())
                raise
        context.write(js_block)
        return

    return decorate
