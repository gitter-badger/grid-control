#-#  Copyright 2009-2015 Karlsruhe Institute of Technology
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

import time
from grid_control import utils
from hpfwk import AbstractError, NestedException, Plugin
from python_compat import set

class ParameterError(NestedException):
	pass


class ParameterInfo:
	reqTypes = ('ACTIVE', 'HASH', 'REQS')
	for idx, reqType in enumerate(reqTypes):
		locals()[reqType] = idx


class ParameterMetadata(str):
	def __new__(cls, value, untracked = False):
		obj = str.__new__(cls, value)
		obj.untracked = untracked
		return obj

	def __repr__(self):
		return "'%s'" % utils.QM(self.untracked, '!%s' % self, self)


class ParameterSource(Plugin):
	def create(cls, pconfig, *args, **kwargs):
		return cls(*args, **kwargs)
	create = classmethod(create)

	def __init__(self):
		self.resyncInfo = None
		self.resyncTime = -1 # Default - always resync
		self.resyncLast = None

	def getMaxParameters(self):
		return None

	def fillParameterKeys(self, result):
		raise AbstractError

	def fillParameterInfo(self, pNum, result):
		raise AbstractError

	def resyncCreate(self):
		return (set(), set(), False) # returns two sets of parameter ids and boolean (redo, disable, sizeChange)

	def resyncSetup(self, interval = None, force = None, info = None):
		self.resyncInfo = info # User override for base resync infos
		if interval != None:
			self.resyncTime = interval # -1 == always, 0 == disabled, >0 == time in sec between resyncs
			self.resyncLast = time.time()
		if force == True:
			self.resyncLast = None # Force resync on next attempt

	def resyncEnabled(self):
		if (self.resyncLast == None) or (self.resyncTime == -1):
			return True
		if self.resyncTime > 0:
			if time.time() - self.resyncLast > self.resyncTime:
				return True
		return False

	def resyncFinished(self):
		self.resyncLast = time.time()

	def resync(self): # needed when parameter values do not change but if meaning / validity of values do
		if self.resyncEnabled() and self.resyncInfo:
			return self.resyncInfo
		return self.resyncCreate()

	def show(self, level = 0, other = ''):
		utils.vprint(('\t' * level) + self.__class__.__name__ + utils.QM(other, ' [%s]' % other, ''), 1)

	def getHash(self):
		raise AbstractError

ParameterSource.managerMap = {}
