#-#  Copyright 2007-2015 Karlsruhe Institute of Technology
#-#
#-#  Licensed under the Apache License, Version 2.0 (the "License");
#-#  you may not use this file except in compliance with the License.
#-#  You may obtain a copy of the License at
#-#
#-#      http://www.apache.org/licenses/LICENSE-2.0
#-#
#-#  Unless required by applicable law or agreed to in writing, software
#-#  distributed under the License is distributed on an "AS IS" BASIS,
#-#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#-#  See the License for the specific language governing permissions and
#-#  limitations under the License.

import os, re, sys, glob, stat, time, Queue, errno, popen2, signal, fnmatch, logging, tarfile, operator, threading
from grid_control.gc_exceptions import InstallationError, RuntimeError, UserError
from grid_control.utils.parsing import parseBool, parseDict, parseInt, parseList, parseStr, parseTime, parseType, strGuid, strTime, strTimeShort
from grid_control.utils.thread_tools import TimeoutException, hang_protection
from hpfwk import APIError
from python_compat import lru_cache, md5, next, set, sorted, user_input

def QM(cond, a, b):
	if cond:
		return a
	return b

################################################################
# Path helper functions

cleanPath = lambda x: os.path.expandvars(os.path.normpath(os.path.expanduser(x.strip())))

def getRootName(fn): # Return file name without extension
	bn = os.path.basename(str(fn)).lstrip('.')
	return QM('.' in bn, str.join('', bn.split('.')[:-1]), bn)

pathGC = lambda *args: cleanPath(os.path.join(os.environ['GC_PACKAGES_PATH'], '..', *args))
pathShare = lambda *args, **kw: cleanPath(os.path.join(os.environ['GC_PACKAGES_PATH'], kw.get('pkg', 'grid_control'), 'share', *args))

def resolvePaths(path, searchPaths = [], mustExist = True, ErrorClass = RuntimeError):
	path = cleanPath(path) # replace $VAR, ~user, \ separators
	result = []
	if os.path.isabs(path):
		result.extend(glob.glob(path)) # Resolve wildcards for existing files
		if not result:
			if mustExist:
				raise ErrorClass('Could not find file "%s"' % path)
			return [path] # Return non-existing, absolute path
	else: # search relative path in search directories
		for spath in set(searchPaths):
			result.extend(glob.glob(cleanPath(os.path.join(spath, path))))
		if not result:
			if mustExist:
				raise ErrorClass('Could not find file "%s" in \n\t%s' % (path, str.join('\n\t', searchPaths)))
			return [path] # Return non-existing, relative path
	return result


def resolvePath(path, searchPaths = [], mustExist = True, ErrorClass = RuntimeError):
	result = resolvePaths(path, searchPaths, mustExist, ErrorClass)
	if len(result) > 1:
		raise ErrorClass('Path "%s" matches multiple files:\n\t%s' % (path, str.join('\n\t', result)))
	return result[0]


def resolveInstallPath(path):
	result = resolvePaths(path, os.environ['PATH'].split(os.pathsep), True, InstallationError)
	result_exe = filter(lambda fn: os.access(fn, os.X_OK), result) # filter executable files
	if not result_exe:
		raise InstallationError('Files matching %s:\n\t%s\nare not executable!' % (path, str.join('\n\t', result_exe)))
	return result_exe[0]


def ensureDirExists(dn, name = 'directory'):
	if not os.path.exists(dn):
		try:
			os.makedirs(dn)
		except Exception:
			raise RuntimeError('Problem creating %s "%s"' % (name, dn))


def freeSpace(dn, timeout = 5):
	def freeSpace_int():
		if os.path.exists(dn):
			try:
				stat_info = os.statvfs(dn)
				return stat_info.f_bavail * stat_info.f_bsize / 1024**2
			except Exception:
				import ctypes
				free_bytes = ctypes.c_ulonglong(0)
				ctypes.windll.kernel32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(dn), None, None, ctypes.pointer(free_bytes))
				return free_bytes.value / 1024**2
		return -1

	try:
		return hang_protection(freeSpace_int, timeout)
	except TimeoutException:
		sys.stderr.write('Unable to get free disk space for directory %s after waiting for %d sec!' % (dn, timeout))
		sys.stderr.write('The file system is probably hanging or corrupted - try to check the free disk space manually.')
		sys.stderr.write('Refer to the documentation to disable checking the free disk space - at your own risk')
		os._exit(os.EX_OSERR)
	return result['space']


################################################################
# Process management functions

def gcStartThread(desc, fun, *args, **kargs):
	thread = threading.Thread(target = fun, args = args, kwargs = kargs)
	thread.setDaemon(True)
	thread.start()
	return thread


def getThreadedGenerator(genList): # Combines multiple, threaded generators into single generator
	(listThread, queue) = ([], Queue.Queue())
	for (desc, gen) in genList:
		def genThread():
			try:
				for item in gen:
					queue.put(item)
			finally:
				queue.put(queue) # Use queue as end-of-generator marker
		listThread.append(gcStartThread(desc, genThread))
	while len(listThread):
		tmp = queue.get(True)
		if tmp == queue:
			listThread.pop()
		else:
			yield tmp

class LoggedProcess(object):
	def __init__(self, cmd, args = '', niceCmd = None, niceArgs = None, shell = True):
		self.niceCmd = QM(niceCmd, niceCmd, os.path.basename(cmd))
		self.niceArgs = QM(niceArgs, niceArgs, args)
		(self.stdout, self.stderr, self.cmd, self.args) = ([], [], cmd, args)
		self._logger = logging.getLogger('process.%s' % os.path.basename(cmd))
		self._logger.log(logging.DEBUG1, 'External programm called: %s %s' % (self.niceCmd, self.niceArgs))
		self.stime = time.time()
		if shell:
			self.proc = popen2.Popen3('%s %s' % (cmd, args), True)
		else:
			if isinstance(cmd, str):
				cmd = [cmd]
			if isinstance(args, str):
				args = args.split()
			self.proc = popen2.Popen3( cmd + list(args), True)

	def getOutput(self, wait = False):
		if wait:
			self.wait()
		self.stdout.extend(self.proc.fromchild.readlines())
		return str.join('', self.stdout)

	def getError(self):
		self.stderr.extend(self.proc.childerr.readlines())
		return str.join('', self.stderr)

	def getMessage(self):
		return self.getOutput() + '\n' + self.getError()

	def kill(self):
		try:
			os.kill(self.proc.pid, signal.SIGTERM)
		except OSError, osError:
			if osError.errno != errno.ESRCH: # errno.ESRCH: no such process (already dead)
				raise

	def iter(self):
		while True:
			try:
				line = self.proc.fromchild.readline()
			except Exception:
				abort(True)
				break
			if not line:
				break
			self.stdout.append(line)
			yield line

	def wait(self, timeout = -1, kill = True):
		if not timeout > 0:
			return self.proc.wait()
		while self.poll() < 0 and timeout > ( time.time() - self.stime ):
			time.sleep(1)
		if kill and timeout > ( time.time() - self.stime ):
			self.kill()
		return self.poll()

	def poll(self):
		return self.proc.poll()

	def getAll(self):
		self.stdout.extend(self.proc.fromchild.readlines())
		self.stderr.extend(self.proc.childerr.readlines())
		return (self.wait(), self.stdout, self.stderr)

	def logError(self, target, brief=False, **kwargs): # Can also log content of additional files via kwargs
		now = time.time()
		entry = '%s.%s' % (time.strftime('%Y-%m-%d_%H:%M:%S', time.localtime(now)), ('%.5f' % (now - int(now)))[2:])
		eprint('WARNING: %s failed with code %d' % (self.niceCmd, self.wait()), printTime=True)
		if not brief:
			eprint('\n%s' % self.getError(), printTime=True)

		try:
			tar = tarfile.TarFile.open(target, 'a')
			data = {'retCode': self.wait(), 'exec': self.cmd, 'args': self.args}
			files = [VirtualFile(os.path.join(entry, 'info'), DictFormat().format(data))]
			kwargs.update({'stdout': self.getOutput(), 'stderr': self.getError()})
			for key, value in kwargs.items():
				try:
					content = open(value, 'r').readlines()
				except Exception:
					content = [value]
				files.append(VirtualFile(os.path.join(entry, key), content))
			for fileObj in files:
				info, handle = fileObj.getTarInfo()
				tar.addfile(info, handle)
				handle.close()
			tar.close()
		except Exception:
			raise RuntimeError('Unable to log errors of external process "%s" to "%s"' % (self.niceCmd, target))
		eprint('All logfiles were moved to %s' % target)


################################################################
# Global state functions

def globalSetupProxy(fun, default, new = None):
	if new != None:
		fun.setting = new
	try:
		return fun.setting
	except Exception:
		return default


def verbosity(new = None):
	return globalSetupProxy(verbosity, 0, new)


def abort(new = None):
	return globalSetupProxy(abort, False, new)


################################################################
# Dictionary tools

def formatDict(d, fmt = '%s=%r', joinStr = ', '):
	return str.join(joinStr, map(lambda k: fmt % (k, d[k]), sorted(d)))


class Result: # Use with caution! Compared with tuples: +25% accessing, 8x slower instantiation
	def __init__(self, **kwargs):
		self.__dict__ = kwargs 
	def __repr__(self):
		return 'Result(%s)' % formatDict(self.__dict__, '%s=%r')


def mergeDicts(dicts):
	tmp = dict()
	for x in dicts:
		tmp.update(x)
	return tmp


def intersectDict(dictA, dictB):
	for keyA in dictA.keys():
		if (keyA in dictB) and (dictA[keyA] != dictB[keyA]):
			dictA.pop(keyA)


def replaceDict(result, allVars, varMapping = None):
	for (virtual, real) in QM(varMapping, varMapping, zip(allVars.keys(), allVars.keys())):
		for delim in ['@', '__']:
			result = result.replace(delim + virtual + delim, str(allVars.get(real, '')))
	return result


def filterDict(dictType, kF = lambda k: True, vF = lambda v: True):
	return dict(filter(lambda (k, v): kF(k) and vF(v), dictType.iteritems()))


class PersistentDict(dict):
	def __init__(self, filename, delimeter = '=', lowerCaseKey = True):
		dict.__init__(self)
		self.fmt = DictFormat(delimeter)
		self.filename = filename
		keyParser = {None: QM(lowerCaseKey, lambda k: parseType(k.lower()), parseType)}
		try:
			self.update(self.fmt.parse(open(filename), keyParser = keyParser))
		except Exception:
			pass
		self.olddict = self.items()

	def get(self, key, default = None, autoUpdate = True):
		value = dict.get(self, key, default)
		if autoUpdate:
			self.write({key: value})
		return value

	def write(self, newdict = {}, update = True):
		if not update:
			self.clear()
		self.update(newdict)
		if dict(self.olddict) == dict(self.items()):
			return
		try:
			if self.filename:
				safeWrite(open(self.filename, 'w'), self.fmt.format(self))
		except Exception:
			raise RuntimeError('Could not write to file %s' % self.filename)
		self.olddict = self.items()


################################################################
# File IO helper

def safeWrite(fp, content):
	fp.writelines(content)
	fp.truncate()
	fp.close()


def removeFiles(args):
	for item in args:
		try:
			if os.path.isdir(item):
				os.rmdir(item)
			else:
				os.unlink(item)
		except Exception:
			pass


################################################################
# String manipulation

def optSplit(opt, delim, empty = ''):
	""" Split option strings into fixed tuples
	>>> optSplit('abc : ghi # def', ['#', ':'])
	('abc', 'def', 'ghi')
	>>> optSplit('abc:def', '::')
	('abc', 'def', '')
	"""
	def getDelimeterPart(oldResult, prefix):
		try:
			tmp = oldResult[0].split(prefix)
			new = tmp.pop(1)
			try: # Find position of other delimeters in string
				otherDelim = min(filter(lambda idx: idx >= 0, map(lambda x: new.find(x), delim)))
				tmp[0] += new[otherDelim:]
			except Exception:
				otherDelim = None
			return [str.join(prefix, tmp)] + oldResult[1:] + [new[:otherDelim]]
		except Exception:
			return oldResult + ['']
	result = map(str.strip, reduce(getDelimeterPart, delim, [opt]))
	return tuple(map(lambda x: QM(x == '', empty, x), result))

################################################################

class TwoSidedIterator(object):
	def __init__(self, allInfo):
		(self.allInfo, self.left, self.right) = (allInfo, 0, 0)
	def forward(self):
		while self.left + self.right < len(self.allInfo):
			self.left += 1
			yield self.allInfo[self.left - 1]
	def backward(self):
		while self.left + self.right < len(self.allInfo):
			self.right += 1
			yield self.allInfo[len(self.allInfo) - self.right]


listMapReduce = lambda fun, lst, start = []: reduce(operator.add, map(fun, lst), start)

def getNamedLogger(prefix, name, instance, postfix = None):
	if not name:
		name = instance.__class__.__name__
	logname = '%s.%s' % (prefix, name.upper().replace('.', '_'))
	if postfix:
		logname = '%s.%s' % (logname, postfix)
	return logging.getLogger(logname)


def checkVar(value, message, check = True):
	if check and max(map(lambda x: max(x.count('@'), x.count('__')), str(value).split('\n'))) >= 2:
		from grid_control.config import ConfigError
		raise ConfigError(message)
	return value


def accumulate(iterable, empty, doEmit, doAdd = lambda item, buffer: True, opAdd = operator.add):
	buffer = empty
	for item in iterable:
		if doAdd(item, buffer):
			buffer = opAdd(buffer, item)
		if doEmit(item, buffer):
			if buffer != empty:
				yield buffer
			buffer = empty
	if buffer != empty:
		yield buffer


def wrapList(value, length, delimLines = ',\n', delimEntries = ', '):
	counter = lambda item, buffer: len(item) + sum(map(len, buffer)) + 2*len(buffer) > length
	wrapped = accumulate(value, [], counter, opAdd = lambda x, y: x + [y])
	return str.join(delimLines, map(lambda x: str.join(delimEntries, x), wrapped))


def flatten(lists):
	result = []
	for x in lists:
		try:
			if isinstance(x, str):
				raise
			result.extend(x)
		except Exception:
			result.append(x)
	return result


def DiffLists(oldList, newList, cmpFkt, changedFkt, isSorted = False):
	(listAdded, listMissing, listChanged) = ([], [], [])
	if not isSorted:
		(newList, oldList) = (sorted(newList, cmpFkt), sorted(oldList, cmpFkt))
	(newIter, oldIter) = (iter(newList), iter(oldList))
	(new, old) = (next(newIter, None), next(oldIter, None))
	while True:
		if (new == None) or (old == None):
			break
		result = cmpFkt(new, old)
		if result < 0: # new[npos] < old[opos]
			listAdded.append(new)
			new = next(newIter, None)
		elif result > 0: # new[npos] > old[opos]
			listMissing.append(old)
			old = next(oldIter, None)
		else: # new[npos] == old[opos] according to *active* comparison
			changedFkt(listAdded, listMissing, listChanged, old, new)
			(new, old) = (next(newIter, None), next(oldIter, None))
	while new != None:
		listAdded.append(new)
		new = next(newIter, None)
	while old != None:
		listMissing.append(old)
		old = next(oldIter, None)
	return (listAdded, listMissing, listChanged)


def rawOrderedBlackWhiteList(value, bwfilter, matcher):
	bwList = map(lambda x: (x, x.startswith('-'), QM(x.startswith('-'), x[1:], x)), bwfilter)
	matchDict = {} # Map for temporary storage of ordered matches - False,True,None are special keys
	for item in value:
		matchExprLast = None
		for (matchOrig, matchBlack, matchExpr) in bwList:
			if matcher(item, matchExpr):
				matchExprLast = matchOrig
		matchDict.setdefault(matchExprLast, []).append(item)
	for (matchOrig, matchBlack, matchExpr) in bwList:
		matchDict.setdefault(matchBlack, []).extend(matchDict.get(matchOrig, []))
	return (matchDict.get(False), matchDict.get(True), matchDict.get(None)) # white, black, unmatched


def filterBlackWhite(value, bwfilter, matcher = str.startswith, addUnmatched = False):
	if (value == None) or (bwfilter == None):
		return None
	(white, black, unmatched) = rawOrderedBlackWhiteList(value, bwfilter, matcher)
	if white != None:
		return white + QM(unmatched and addUnmatched, unmatched, [])
	return QM(unmatched and bwfilter, unmatched, [])


def splitBlackWhiteList(bwfilter):
	blacklist = map(lambda x: x[1:], filter(lambda x: x.startswith('-'), QM(bwfilter, bwfilter, [])))
	whitelist = filter(lambda x: not x.startswith('-'), QM(bwfilter, bwfilter, []))
	return (blacklist, whitelist)


def doBlackWhiteList(value, bwfilter, matcher = str.startswith, onEmpty = None, preferWL = True):
	""" Apply black-whitelisting to input list
	>>> (il, f) = (['T2_US_MIT', 'T1_DE_KIT_MSS', 'T1_US_FNAL'], ['T1', '-T1_DE_KIT'])
	>>> (doBlackWhiteList(il,    f), doBlackWhiteList([],    f), doBlackWhiteList(None,    f))
	(['T1_US_FNAL'], ['T1'], ['T1'])
	>>> (doBlackWhiteList(il,   []), doBlackWhiteList([],   []), doBlackWhiteList(None,   []))
	(['T2_US_MIT', 'T1_DE_KIT_MSS', 'T1_US_FNAL'], None, None)
	>>> (doBlackWhiteList(il, None), doBlackWhiteList([], None), doBlackWhiteList(None, None))
	(['T2_US_MIT', 'T1_DE_KIT_MSS', 'T1_US_FNAL'], None, None)
	"""
	(blacklist, whitelist) = splitBlackWhiteList(bwfilter)
	checkMatch = lambda item, matchList: True in map(lambda x: matcher(item, x), matchList)
	value = filter(lambda x: not checkMatch(x, blacklist), QM(value or not preferWL, value, whitelist))
	if len(whitelist):
		return filter(lambda x: checkMatch(x, whitelist), value)
	return QM(value or bwfilter, value, onEmpty)


class DictFormat(object):
	# escapeString = escape '"', '$'
	# types = preserve type information
	def __init__(self, delimeter = '=', escapeString = False):
		self.delimeter = delimeter
		self.escapeString = escapeString

	# Parse dictionary lists
	def parse(self, lines, keyParser = {}, valueParser = {}):
		defaultKeyParser = keyParser.get(None, lambda k: parseType(k.lower()))
		defaultValueParser = valueParser.get(None, parseType)
		data = {}
		doAdd = False
		currentline = ''
		try:
			lines = lines.splitlines()
		except Exception:
			pass
		for line in lines:
			if self.escapeString:
				# Switch accumulate on/off when odd number of quotes found
				if (line.count('"') - line.count('\\"')) % 2 == 1:
					doAdd = not doAdd
				currentline += line
				if doAdd:
					continue
			else:
				currentline = line
			try: # split at first occurence of delimeter and strip spaces around
				key, value = map(str.strip, currentline.split(self.delimeter, 1))
				currentline = ''
			except Exception: # in case no delimeter was found
				currentline = ''
				continue
			if self.escapeString:
				value = value.strip('"').replace('\\"', '"').replace('\\$', '$')
			key = keyParser.get(key, defaultKeyParser)(key)
			data[key] = valueParser.get(key, defaultValueParser)(value) # do .encode('utf-8') ?
		if doAdd:
			raise RuntimeError('Invalid dict format in %s' % fp.name)
		return data

	# Format dictionary list
	def format(self, entries, printNone = False, fkt = lambda (x, y, z): (x, y, z), format = '%s%s%s\n'):
		result = []
		for key in entries.keys():
			value = entries[key]
			if value == None and not printNone:
				continue
			if self.escapeString and isinstance(value, str):
				value = '"%s"' % str(value).replace('"', '\\"').replace('$', '\\$')
				lines = value.splitlines()
				result.append(format % fkt((key, self.delimeter, lines[0])))
				result.extend(map(lambda x: x + '\n', lines[1:]))
			else:
				result.append(format % fkt((key, self.delimeter, value)))
		return result


def matchFileName(fn, patList):
	match = None
	for p in patList:
		if fnmatch.fnmatch(fn, p.lstrip('-')):
			match = not p.startswith('-')
	return match


def matchFiles(pathRoot, pattern, pathRel = ''):
	# Return (root, fn, state) - state: None == dir, True/False = (un)checked file, other = filehandle
	yield (pathRoot, pathRel, None)
	for name in map(lambda x: os.path.join(pathRel, x), os.listdir(os.path.join(pathRoot, pathRel))):
		match = matchFileName(name, pattern)
		pathAbs = os.path.join(pathRoot, name)
		if match == False:
			continue
		elif os.path.islink(pathAbs): # Not excluded symlinks
			yield (pathAbs, name, True)
		elif os.path.isdir(pathAbs): # Recurse into directories
			if match == True: # (backwards compat: add parent directory - not needed?)
				yield (pathAbs, name, True)
			for result in matchFiles(pathRoot, QM(match == True, ['*'], pattern), name):
				yield result
		elif match == True: # Add matches
			yield (pathAbs, name, True)


def genTarball(outFile, fileList):
	tar = tarfile.open(outFile, 'w:gz')
	activity = None
	for (pathAbs, pathRel, pathStatus) in fileList:
		if pathStatus == True: # Existing file
			tar.add(pathAbs, pathRel, recursive = False)
		elif pathStatus == False: # Existing file
			if not os.path.exists(pathAbs):
				raise UserError('File %s does not exist!' % pathRel)
			tar.add(pathAbs, pathRel, recursive = False)
		elif pathStatus == None: # Directory
			del activity
			msg = QM(len(pathRel) > 50, pathRel[:15] + '...' + pathRel[len(pathRel)-32:], pathRel)
			activity = ActivityLog('Generating tarball: %s' % msg)
		else: # File handle
			info, handle = pathStatus.getTarInfo()
			info.mtime = time.time()
			info.mode = stat.S_IRUSR + stat.S_IWUSR + stat.S_IRGRP + stat.S_IROTH
			if info.name.endswith('.sh') or info.name.endswith('.py'):
				info.mode += stat.S_IXUSR + stat.S_IXGRP + stat.S_IXOTH
			tar.addfile(info, handle)
			handle.close()
	del activity
	tar.close()


def vprint(text = '', level = 0, printTime = False, newline = True, once = False):
	if verbosity() > level:
		if once:
			if text in vprint.log:
				return
			vprint.log.append(text)
		if printTime:
			sys.stdout.write('%s - ' % time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()))
		sys.stdout.write('%s%s' % (text, QM(newline, '\n', '')))
vprint.log = []


def eprint(text = '', level = -1, printTime = False, newline = True):
	if verbosity() > level:
		if printTime:
			sys.stderr.write('%s - ' % time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()))
		sys.stderr.write('%s%s' % (text, QM(newline, '\n', '')))


def getVersion():
	try:
		version = LoggedProcess('svnversion', '-c %s' % pathGC()).getOutput(True).strip()
		if version != '':
			if 'stable' in LoggedProcess('svn info', pathGC()).getOutput(True):
				return '%s - stable' % version
			return '%s - testing' % version
	except Exception:
		pass
	return 'unknown'
getVersion = lru_cache(getVersion)


def wait(timeout):
	shortStep = map(lambda x: (x, 1), range(max(timeout - 5, 0), timeout))
	for x, w in map(lambda x: (x, 5), range(0, timeout - 5, 5)) + shortStep:
		if abort():
			return False
		log = ActivityLog('waiting for %d seconds' % (timeout - x))
		time.sleep(w)
		del log
	return True


class ActivityLog:
	class Activity:
		def __init__(self, stream, message):
			self.stream = stream
			self.message = '%s...' % message
			self.status = False

		def run(self):
			if not self.status:
				self.stream.write(self.message)
				self.stream.flush()
				self.status = True

		def clear(self):
			if self.status:
				self.stream.write('\r%s\r' % (' ' * len(self.message)))
				self.stream.flush()
				self.status = False

	class WrappedStream:
		def __init__(self, stream, activity):
			self.__stream = stream
			self.__activity = activity
			self.__activity.run()

		def __del__(self):
			self.__activity.clear()

		def write(self, data):
			self.__activity.clear()
			retVal = self.__stream.write(data)
			if data.endswith('\n'):
				self.__activity.run()
			return retVal

		def __getattr__(self, name):
			return self.__stream.__getattribute__(name)

	def __init__(self, message):
		self.saved = (sys.stdout, sys.stderr)
		if sys.stdout.isatty():
			self.activity = self.Activity(sys.stdout, message)
			sys.stdout = self.WrappedStream(sys.stdout, self.activity)
			sys.stderr = self.WrappedStream(sys.stderr, self.activity)

	def __del__(self):
		sys.stdout, sys.stderr = self.saved


def printTabular(head, data, fmtString = '', fmt = {}, level = -1):
	if printTabular.mode == 'parseable':
		vprint(str.join("|", map(lambda x: x[1], head)), level)
		for entry in data:
			if isinstance(entry, dict):
				vprint(str.join("|", map(lambda x: str(entry.get(x[0], '')), head)), level)
		return
	if printTabular.mode == 'longlist':
		maxhead = max(map(len, map(lambda (key, name): name, head)))
		showLine = False
		for entry in data:
			if isinstance(entry, dict):
				if showLine:
					vprint((('-' * (maxhead + 2)) + '-+-' + '-' * min(30, printTabular.wraplen - maxhead - 10)), level)
				for (key, name) in head:
					vprint((name.rjust(maxhead + 2) + ' | ' + str(fmt.get(key, str)(entry.get(key, '')))), level)
				showLine = True
			elif showLine:
				vprint((('=' * (maxhead + 2)) + '=+=' + '=' * min(30, printTabular.wraplen - maxhead - 10)), level)
				showLine = False
		return

	justFunDict = { 'l': str.ljust, 'r': str.rjust, 'c': str.center }
	# justFun = {id1: str.center, id2: str.rjust, ...}
	head = list(head)
	justFun = dict(map(lambda (idx, x): (idx[0], justFunDict[x]), zip(head, fmtString)))

	# adjust to lendict of column (considering escape sequence correction)
	strippedlen = lambda x: len(re.sub('\33\[\d*(;\d*)*m', '', x))
	just = lambda key, x: justFun.get(key, str.rjust)(x, lendict[key] + len(x) - strippedlen(x))

	lendict = dict(map(lambda (key, name): (key, len(name)), head))

	entries = [] # formatted, but not yet aligned entries
	for entry in data:
		if isinstance(entry, dict):
			tmp = {}
			for key, name in head:
				tmp[key] = str(fmt.get(key, str)(entry.get(key, '')))
				lendict[key] = max(lendict[key], strippedlen(tmp[key]))
			entries.append(tmp)
		else:
			entries.append(entry)

	def getGoodPartition(keys, lendict, maxlen): # BestPartition => NP complete
		def getFitting(leftkeys):
			current = 0
			for key in leftkeys:
				if current + lendict[key] <= maxlen:
					current += lendict[key]
					yield key
			if current == 0:
				yield leftkeys[0]
		unused = list(keys)
		while len(unused) != 0:
			for key in list(getFitting(unused)): # list(...) => get fitting keys at once!
				if key in unused:
					unused.remove(key)
				yield key
			yield None

	def getAlignedDict(keys, lendict, maxlen):
		edges = []
		while len(keys):
			offset = 2
			(tmp, keys) = (keys[:keys.index(None)], keys[keys.index(None)+1:])
			for key in tmp:
				left = max(0, maxlen - sum(map(lambda k: lendict[k] + 3, tmp)))
				for edge in edges:
					if (edge > offset + lendict[key]) and (edge - (offset + lendict[key]) < left):
						lendict[key] += edge - (offset + lendict[key])
						left -= edge - (offset + lendict[key])
						break
				edges.append(offset + lendict[key])
				offset += lendict[key] + 3
		return lendict

	# Wrap and align columns
	headwrap = list(getGoodPartition(map(lambda (key, name): key, head),
		dict(map(lambda (k, v): (k, v + 2), lendict.items())), printTabular.wraplen))
	lendict = getAlignedDict(headwrap, lendict, printTabular.wraplen)

	headentry = dict(map(lambda (id, name): (id, name.center(lendict[id])), head))
	# Wrap rows
	def wrapentries(entries):
		for idx, entry in enumerate(entries):
			def doEntry(entry):
				tmp = []
				for key in headwrap:
					if key == None:
						yield (tmp, entry)
						tmp = []
					else:
						tmp.append(key)
			if not isinstance(entry, str):
				for x in doEntry(entry):
					yield x
				if (idx != 0) and (idx != len(entries) - 1):
					if None in headwrap[:-1]:
						yield list(doEntry("~"))[0]
			else:
				yield list(doEntry(entry))[0]

	for (keys, entry) in wrapentries([headentry, "="] + entries):
		if isinstance(entry, str):
			decor = lambda x: "%s%s%s" % (entry, x, entry)
			vprint(decor(str.join(decor('+'), map(lambda key: entry * lendict[key], keys))), level)
		else:
			vprint(' %s ' % str.join(' | ', map(lambda key: just(key, entry.get(key, '')), keys)), level)
printTabular.wraplen = 100
printTabular.mode = 'default'


def getUserInput(text, default, choices, parser = lambda x: x):
	while True:
		try:
			userinput = user_input('%s %s: ' % (text, '[%s]' % default))
		except Exception:
			eprint()
			sys.exit(os.EX_OK)
		if userinput == '':
			return parser(default)
		if parser(userinput) != None:
			return parser(userinput)
		valid = str.join(', ', map(lambda x: '"%s"' % x, choices[:-1]))
		eprint('Invalid input! Answer with %s or "%s"' % (valid, choices[-1]))


def getUserBool(text, default):
	return getUserInput(text, QM(default, 'yes', 'no'), ['yes', 'no'], parseBool)


def deprecated(text):
	eprint('%s\n[DEPRECATED] %s' % (open(pathShare('fail.txt'), 'r').read(), text))
	if not getUserBool('Do you want to continue?', False):
		sys.exit(os.EX_TEMPFAIL)


def exitWithUsage(usage, msg = None, helpOpt = True):
	sys.stderr.write(QM(msg, '%s\n' % msg, ''))
	sys.stderr.write('Syntax: %s\n%s' % (usage, QM(helpOpt, 'Use --help to get a list of options!\n', '')))
	sys.exit(os.EX_USAGE)


def makeEnum(members = [], cls = None, useHash = False):
	if cls:
		enumID = md5(str(members) + '!' + cls.__name__).hexdigest()[:4]
	else:
		enumID = md5(str(members)).hexdigest()[:4]
		cls = type('Enum_%s_%s' % (enumID, str.join('_', members)), (), {})

	if useHash:
		getValue = lambda (idx, name): idx + int(enumID, 16)
	else:
		getValue = lambda (idx, name): idx
	values = list(map(getValue, enumerate(members)))

	cls.enumNames = members
	cls.enumValues = values
	cls._enumMapNV = dict(zip(cls.enumNames, cls.enumValues))
	cls._enumMapVN = dict(zip(cls.enumValues, cls.enumNames))
	if len(cls._enumMapNV) != len(cls._enumMapVN):
		raise APIError('Invalid enum definition!')
	cls.enum2str = cls._enumMapVN.get
	cls.str2enum = cls._enumMapNV.get
	for name, value in zip(cls.enumNames, cls.enumValues):
		setattr(cls, name, value)
	return cls


def split_advanced(tokens, doEmit, addEmitToken, quotes = ['"', "'"], brackets = ['()', '{}', '[]'], exType = Exception):
	buffer = ''
	emit_empty_buffer = False
	stack_quote = []
	stack_bracket = []
	map_openbracket = dict(map(lambda x: (x[1], x[0]), brackets))
	tokens = iter(tokens)
	token = next(tokens, None)
	while token:
		emit_empty_buffer = False
		# take care of quotations
		if token in quotes:
			if stack_quote and stack_quote[-1] == token:
				stack_quote.pop()
			else:
				stack_quote.append(token)
		if stack_quote:
			buffer += token
			token = next(tokens, None)
			continue
		# take care of parentheses
		if token in map_openbracket.values():
			stack_bracket.append(token)
		if token in map_openbracket.keys():
			if stack_bracket[-1] == map_openbracket[token]:
				stack_bracket.pop()
			else:
				raise ExType('Uneven brackets!')
		if stack_bracket:
			buffer += token
			token = next(tokens, None)
			continue
		# take care of low level splitting
		if not doEmit(token):
			buffer += token
			token = next(tokens, None)
			continue
		if addEmitToken(token):
			buffer += token
		else: # if tokenlist ends with emit token, which is not emited, finish with empty buffer
			emit_empty_buffer = True
		yield buffer
		buffer = ''
		token = next(tokens, None)

	if stack_quote or stack_bracket:
		raise ExType('Brackets / quotes not closed!')
	if buffer or emit_empty_buffer:
		yield buffer


def ping_host(host):
	try:
		tmp = LoggedProcess('ping', '-Uqnc 1 -W 1 %s' % host).getOutput().splitlines()
		assert(tmp[-1].endswith('ms'))
		return float(tmp[-1].split('/')[-2]) / 1000.
	except Exception:
		return None


if __name__ == '__main__':
	import doctest
	doctest.testmod()
