#-#  Copyright 2012-2014 Karlsruhe Institute of Technology
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

from grid_control import utils
from grid_control.backends.broker import Broker
from grid_control.backends.wms import WMS
from python_compat import sorted

class RandomBroker(Broker):
	def __init__(self, config, name, userOpt, itemName, discoverFun):
		Broker.__init__(self, config, name, userOpt, itemName, discoverFun)
		self._itemsStart = self._discover(discoverFun)


class UserBroker(Broker):
	def __init__(self, config, name, userOpt, itemName, discoverFun):
		Broker.__init__(self, config, name, userOpt, itemName, discoverFun)
		self._itemsStart = config.getList(userOpt, [], onChange = None)
		if not self._itemsStart:
			self._itemsStart = None


class FilterBroker(UserBroker):
	def __init__(self, config, name, userOpt, itemName, discoverFun):
		UserBroker.__init__(self, config, name, userOpt, itemName, discoverFun)
		if self._itemsStart:
			self._discover(discoverFun)
		if self._itemsDiscovered:
			self._itemsStart = utils.filterBlackWhite(self._itemsDiscovered, self._itemsStart, addUnmatched = True)


class CoverageBroker(Broker):
	def __init__(self, config, name, userOpt, itemName, discoverFun):
		Broker.__init__(self, config, name, userOpt, itemName, discoverFun)
		itemsUser = config.getList(userOpt, [], onChange = None)
		if not itemsUser:
			itemsUser = None
		itemsDisc = self._discover(discoverFun).keys()
		self._itemsStart = itemsDisc
		if itemsDisc and itemsUser:
			self._itemsStart = utils.filterBlackWhite(itemsDisc, itemsUser)
		elif not itemsDisc:
			self._itemsStart = utils.filterBlackWhite(itemsUser, itemsUser)
		self._nIndex = 0

	def _broker(self, reqs, items):
		items = Broker._broker(self, reqs, items)
		if items:
			items = [items[self._nIndex % len(items)]]
			self._nIndex += 1
		return items


class SimpleBroker(FilterBroker):
	def __init__(self, config, name, userOpt, itemName, discoverFun):
		FilterBroker.__init__(self, config, name, userOpt, itemName, discoverFun)
		self._discover(discoverFun)

		def item_cmp(a, b, cmp_fun = cmp):
			diff = 0 # Return negative if a < b, zero if a == b, positive if a > b
			for key in a.keys() + b.keys():
				current_diff = 1
				if key in a and key in b:
					current_diff = cmp_fun(a[key], b[key])
				elif key in a:
					current_diff = -1
				if (diff != current_diff) and (diff == 0):
					diff = current_diff
				elif (diff != current_diff) and (current_diff != 0):
					return 0
			return diff

		if self._itemsDiscovered: # Sort discovered items according to requirements
			self._itemsSorted = sorted(self._itemsDiscovered, item_cmp, lambda x: self._itemsDiscovered[x])

	def _broker(self, reqs, items):
		if not self._itemsDiscovered:
			return FilterBroker._broker(self, reqs, self._itemsStart) # Use user constrained items
		items = utils.QM(self._itemsStart != None, self._itemsStart, self._itemsSorted) # or discovered items

		# Match items which fulfill the requirements
		def matcher(props, cmp_fun = cmp):
			for key, value in reqs:
				if key not in props:
					continue
				if cmp_fun(value, props[key]) >= 0:
					return False
			return True
		# Apply sort order and give matching entries as preselection to FilterBroker
		items = filter(lambda x: matcher(self._itemsDiscovered[x]), items)
		return FilterBroker._broker(self, reqs, filter(lambda x: x in items, self._itemsSorted))


class StorageBroker(Broker):
	def __init__(self, config, name, userOpt, itemName, discoverFun):
		Broker.__init__(self, config, name, userOpt, itemName, discoverFun)
		self._storageDict = config.getDict('%s storage access' % userOpt, {}, onChange = None,
			parser = lambda x: utils.parseList(x, ' '), strfun = lambda x: str.join(' ', x))[0]

	def _broker(self, reqs, items):
		result = Broker._broker(self, reqs, items)
		for (rType, rValue) in reqs:
			if (rType == WMS.STORAGE) and rValue:
				if result == None:
					result = []
				for rval in rValue:
					result.extend(self._storageDict.get(rval, []))
		return result
