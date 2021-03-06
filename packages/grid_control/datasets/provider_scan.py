#-#  Copyright 2010-2015 Karlsruhe Institute of Technology
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

import os, sys
from grid_control import utils
from grid_control.config import createConfigFactory
from grid_control.datasets.provider_base import DataProvider
from grid_control.datasets.scanner_base import InfoScanner
from python_compat import md5, set

class ScanProviderBase(DataProvider):
	def __init__(self, config, datasetExpr, datasetNick, datasetID = 0):
		DataProvider.__init__(self, config, '', datasetNick, datasetID)
		DSB = lambda cFun, n, *args, **kargs: (cFun('dataset %s' % n, *args, **kargs),
			cFun('block %s' % n, *args, **kargs))
		(self.nameDS, self.nameB) = DSB(config.get, 'name pattern', '')
		(self.kUserDS, self.kUserB) = DSB(config.getList, 'hash keys', [])
		(self.kGuardDS, self.kGuardB) = DSB(config.getList, 'guard override', [])
		self.kSelectDS = config.getList('dataset key select', [])
		scanList = config.getList('scanner', datasetExpr)
		self.scanner = map(lambda cls: InfoScanner.getInstance(cls, config), scanList)


	def collectFiles(self):
		def recurse(level, collectorList, args):
			if len(collectorList):
				for data in recurse(level - 1, collectorList[:-1], args):
					for (path, metadata, nEvents, seList, objStore) in collectorList[-1](level, *data):
						yield (path, dict(metadata), nEvents, seList, objStore)
			else:
				yield args
		return recurse(len(self.scanner), map(lambda x: x.getEntriesVerbose, self.scanner), (None, {}, None, None, {}))


	def generateKey(self, keys, base, path, metadata, events, seList, objStore):
		return md5(repr(base) + repr(seList) + repr(map(lambda k: metadata.get(k, None), keys))).hexdigest()


	def generateDatasetName(self, key, data):
		if 'SE_OUTPUT_BASE' in data:
			return utils.replaceDict(utils.QM(self.nameDS, self.nameDS, '/PRIVATE/@SE_OUTPUT_BASE@'), data)
		return utils.replaceDict(utils.QM(self.nameDS, self.nameDS, '/PRIVATE/Dataset_%s' % key), data)


	def generateBlockName(self, key, data):
		return utils.replaceDict(utils.QM(self.nameB, self.nameB, key[:8]), data)


	def getBlocksInternal(self):
		# Split files into blocks/datasets via key functions and determine metadata intersection
		(protoBlocks, commonDS, commonB) = ({}, {}, {})
		def getActiveKeys(kUser, kGuard, gIdx):
			return kUser + utils.QM(kGuard, kGuard, utils.listMapReduce(lambda x: x.getGuards()[gIdx], self.scanner))
		keysDS = getActiveKeys(self.kUserDS, self.kGuardDS, 0)
		keysB = getActiveKeys(self.kUserB, self.kGuardB, 1)
		for fileInfo in self.collectFiles():
			hashDS = self.generateKey(keysDS, None, *fileInfo)
			hashB = self.generateKey(keysB, hashDS, *fileInfo)
			if self.kSelectDS and (hashDS not in self.kSelectDS):
				continue
			fileInfo[1].update({'DS_KEY': hashDS, 'BLOCK_KEY': hashB})
			protoBlocks.setdefault(hashDS, {}).setdefault(hashB, []).append(fileInfo)
			utils.intersectDict(commonDS.setdefault(hashDS, dict(fileInfo[1])), fileInfo[1])
			utils.intersectDict(commonB.setdefault(hashDS, {}).setdefault(hashB, dict(fileInfo[1])), fileInfo[1])

		# Generate names for blocks/datasets using common metadata
		(hashNameDictDS, hashNameDictB) = ({}, {})
		for hashDS in protoBlocks:
			hashNameDictDS[hashDS] = self.generateDatasetName(hashDS, commonDS[hashDS])
			for hashB in protoBlocks[hashDS]:
				hashNameDictB[hashB] = (hashDS, self.generateBlockName(hashB, commonB[hashDS][hashB]))

		# Find name <-> key collisions
		def findCollision(tName, nameDict, varDict, hashKeys, keyFmt = lambda x: x):
			targetNames = nameDict.values()
			for name in list(set(targetNames)):
				targetNames.remove(name)
			if len(targetNames):
				ask = True
				for name in targetNames:
					utils.eprint("Multiple %s keys are mapped to the same %s name '%s'!" % (tName, tName, keyFmt(name)))
					for key in nameDict:
						if nameDict[key] == name:
							utils.eprint('\t%s hash %s using:' % (tName, keyFmt(key)))
							for x in filter(lambda (k, v): k in hashKeys, varDict[keyFmt(key)].items()):
								utils.eprint('\t\t%s = %s' % x)
					if ask and not utils.getUserBool('Do you want to continue?', False):
						sys.exit(os.EX_OK)
					ask = False
		findCollision('dataset', hashNameDictDS, commonDS, keysDS)
		findCollision('block', hashNameDictB, commonB, keysB, lambda x: x[1])

		# Return named dataset
		for hashDS in protoBlocks:
			for hashB in protoBlocks[hashDS]:
				blockSEList = None
				for seList in filter(lambda s: s != None, map(lambda x: x[3], protoBlocks[hashDS][hashB])):
					blockSEList = utils.QM(blockSEList == None, [], blockSEList)
					blockSEList.extend(seList)
				if blockSEList != None:
					blockSEList = list(set(blockSEList))
				metaKeys = protoBlocks[hashDS][hashB][0][1].keys()
				fnProps = lambda (path, metadata, events, seList, objStore): {
					DataProvider.URL: path, DataProvider.NEntries: events,
					DataProvider.Metadata: map(lambda x: metadata.get(x), metaKeys)}
				yield {
					DataProvider.Dataset: hashNameDictDS[hashDS],
					DataProvider.BlockName: hashNameDictB[hashB][1],
					DataProvider.Locations: blockSEList,
					DataProvider.Metadata: metaKeys,
					DataProvider.FileList: map(fnProps, protoBlocks[hashDS][hashB])
				}


# Get dataset information from storage url
# required format: <storage url>
class ScanProvider(ScanProviderBase):
	DataProvider.providers.update({'ScanProvider': 'scan'})
	def __init__(self, config, datasetExpr, datasetNick, datasetID = 0):
		if '*' in os.path.basename(datasetExpr):
			config.set('source directory', os.path.dirname(datasetExpr))
			config.set('filename filter', datasetExpr)
		else:
			config.set('source directory', datasetExpr)
		defScanner = ['FilesFromLS', 'MatchOnFilename', 'MatchDelimeter', 'DetermineEvents', 'AddFilePrefix']
		ScanProviderBase.__init__(self, config, defScanner, datasetNick, datasetID)


# Get dataset information just from grid-control instance
# required format: <path to config file / workdir> [%<job selector]
class GCProvider(ScanProviderBase):
	DataProvider.providers.update({'GCProvider': 'gc'})
	stageDir = {}
	stageFile = {None: ['MatchOnFilename', 'MatchDelimeter']}

	def __init__(self, config, datasetExpr, datasetNick, datasetID = 0):
		if os.path.isdir(datasetExpr):
			GCProvider.stageDir[None] = ['OutputDirsFromWork']
			config.set('source directory', datasetExpr)
			datasetExpr = os.path.join(datasetExpr, 'work.conf')
		else:
			GCProvider.stageDir[None] = ['OutputDirsFromConfig', 'MetadataFromTask']
			datasetExpr, selector = utils.optSplit(datasetExpr, '%')
			config.set('source config', datasetExpr)
			config.set('source job selector', selector)
		extConfig = createConfigFactory(datasetExpr).getConfig()
		extModule = extConfig.changeView(setSections = ['global']).get(['task', 'module'])
		if 'ParaMod' in extModule: # handle old config files
			extModule = extConfig.changeView(setSections = ['ParaMod']).get('module')
		sGet = lambda scannerDict: scannerDict.get(None) + scannerDict.get(extModule, [])
		sList = sGet(GCProvider.stageDir) + ['JobInfoFromOutputDir', 'FilesFromJobInfo'] + sGet(GCProvider.stageFile) + ['DetermineEvents', 'AddFilePrefix']
		ScanProviderBase.__init__(self, config, sList, datasetNick, datasetID)
