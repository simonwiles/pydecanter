"""

Monitors changes to python files

(code based on an example by Graham Dumpleton at:
   http://code.google.com/p/modwsgi/wiki/ReloadingSourceCode )

Usage example:

    # define a function to execute when changes are detected
    def actions():
        # perform actions
        pass

    monitor = Monitor(interval=1.0)
    monitor.on_modified = actions

    # additional (non-Python) files can be tracked too, but must be added
    #  individually (e.g. os.path.walk)
    monitor.track('./')

"""

import os
import sys
import threading
import atexit
try:
    import queue            # python 3.x
except ImportError:
     import Queue as queue  # python 2.x

class Monitor(object):

    def __init__(self, interval=1.0):

        self._times = {}
        self._files = []

        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._interval = interval

        self._thread = threading.Thread(target=self._monitor)
        self._thread.setDaemon(True)

        self._lock.acquire()

        self._thread.start()
        self._lock.release()

        atexit.register(self._exiting)

    def _on_modified(self, path):
        self._queue.put(True)
        self.on_modified(path)

    def _modified(self, path):
        try:
            # If path doesn't denote a file and we were previously
            # tracking it, then it has been removed or the file type
            # has changed so force a restart. If not previously
            # tracking the file then we can ignore it as probably
            # pseudo reference such as when file extracted from a
            # collection of modules contained in a zip file.
            if not os.path.isfile(path):
                return path in self._times

            # Check for when file last modified.
            mtime = os.stat(path).st_mtime
            if path not in self._times:
                self._times[path] = mtime

            # Force restart when modification time has changed, even
            # if time now older, as that could indicate older file
            # has been restored.
            if mtime != self._times[path]:
                return True
        except:
            raise
            # If any exception occured, likely that file has been
            # been removed just before stat(), so force a restart.
            return True

        return False

    def _monitor(self):
        while 1:
            # Check modification times on all files in sys.modules.
            for module in list(sys.modules.values()):
                if not hasattr(module, '__file__'):
                    continue
                path = getattr(module, '__file__')
                if not path:
                    continue
                if os.path.splitext(path)[1] in ('.pyc', '.pyo', '.pyd'):
                    path = path[:-1]
                if self._modified(path):
                    return self._on_modified(path)

            # Check modification times on files which have
            # specifically been registered for monitoring.
            for path in self._files:
                if self._modified(path):
                    return self._on_modified(path)

            # Go to sleep for specified interval.
            try:
                return self._queue.get(timeout=self._interval)
            except:
                pass

    def _exiting(self):
        try:
            self._queue.put(True)
        except:
            pass
        self._thread.join()

    def track(self, path):
        if not path in self._files:
            self._files.append(path)

    def on_modified(self):
        """ Method to be overwritten """
        pass
