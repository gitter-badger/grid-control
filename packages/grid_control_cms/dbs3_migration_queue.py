#-#  Copyright 2014-2015 Karlsruhe Institute of Technology
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

import cPickle as pickle
import logging
from Queue import Empty, Queue
from collections import deque
from time import time
from python_compat import set

class MigrationRequestedState(object):
    def __init__(self, migration_task):
        self.migration_task = migration_task

    def run(self):
        #submit task to DBS 3 migration
        try:
            self.migration_task.migration_request = \
                self.migration_task.dbs_client.migrateSubmit(self.migration_task.payload)
        except AttributeError:
            #simulation
            self.migration_task.logger.info("%s has been queued for migration!" % self.migration_task)
        else:
            self.migration_task.logger.info("%s has been queued for migration!" % self.migration_task)

        self.migration_task.state = MigrationSubmittedState(self.migration_task)


class MigrationSubmittedState(object):
    def __init__(self, migration_task):
        self.migration_task = migration_task
        self.max_poll_interval = 10
        self.last_poll_time = time()

    def run(self):
        if abs(self.last_poll_time-time()) > self.max_poll_interval:
            #check migration status
            try:
                migration_request_id = self.migration_task.migration_request['migration_details']['migration_request_id']
                request_status = self.migration_task.dbs_client.migrateStatus(migration_rqst_id=migration_request_id)
                self.migration_task.logger.debug("%s has migration_status=%s"
                                                 % (self.migration_task, request_status[0]['migration_status']))
            except AttributeError:
                #simulation
                print "Simulation"
                request_status = [{'migration_status': 2}]
                self.migration_task.logger.debug("%s has migration_status=%s"
                                                 % (self.migration_task, request_status[0]['migration_status']))
            finally:
                self.last_poll_time = time()

            if request_status[0]['migration_status'] == 2:
                #migration okay
                self.migration_task.state.__class__ = MigrationDoneState
            elif request_status[0]['migration_status'] == 9:
                #migration failed
                self.migration_task.state.__class__ = MigrationFailedState


class MigrationDoneState(object):
    def __init__(self, migration_task):
        self.migration_task = migration_task

    def run(self):
        self.migration_task.logger.info("%s is done!" % self.migration_task)


class MigrationFailedState(object):
    def __init__(self, migration_task):
        self.migration_task = migration_task

    def run(self):
        self.migration_task.logger.error("%s failed! Please contact DBS admin!" % self.migration_task)
        raise MigrationFailed("%s failed! Please contact DBS admin!" % self.migration_task)


class MigrationTask(object):
    def __init__(self, block_name, migration_url, dbs_client):
        self.block_name = block_name
        self.migration_url = migration_url
        self.dbs_client = dbs_client
        self.logger = logging.getLogger('dbs3-migration')

        self.state = MigrationRequestedState(self)

    def run(self):
        self.state.run()

    def is_done(self):
        return self.state.__class__ == MigrationDoneState

    def is_failed(self):
        return self.state.__class__ == MigrationFailedState

    @property
    def payload(self):
        return {'migration_url': self.migration_url,
                'migration_input': self.block_name}

    def __eq__(self, other):
        return (self.block_name == other.block_name) and (self.migration_url == other.migration_url)

    def __ne__(self, other):
        return (self.block_name != other.block_name) or (self.migration_url != other.migration_url)

    def __hash__(self):
        return hash(self.block_name+self.migration_url)

    def __str__(self):
        return repr(self)

    def __repr__(self):
        return '%s(block_name="%s", migration_url="%s")' % (self.__class__.__name__, self.block_name,
                                                            self.migration_url)

    def __getstate__(self):
        """
        Logger object cannot be pickled
        """
        state = dict(self.__dict__)
        del state['logger']
        return state

    def __setstate__(self, state):
        """
        Logger object cannot be pickled, restore logger attribute
        """
        state['logger'] = logging.getLogger('dbs3-migration')
        self.__dict__.update(state)
        return True


class AlreadyQueued(Exception):
    def __init__(self, message):
        self.message = message
        super(AlreadyQueued, self).__init__(self, "AlreadyQueuedException %s" % self.message)

    def __repr__(self):
        return '%s %r' % (self.__class__.__name__, self.message)

    def __str__(self):
        return repr(self.message)


class MigrationFailed(Exception):
    def __init__(self, message):
        self.message = message
        super(MigrationFailed, self).__init__(self, "MigrationFailedException %s" % self.message)

    def __repr__(self):
        return '%s %r' % (self.__class__.__name__, self.message)

    def __str__(self):
        return repr(self.message)


class DBS3MigrationQueue(deque):
    _unique_queued_tasks = set()

    def __init__(self, tasks=None, maxlen=None):
        super(DBS3MigrationQueue, self).__init__(iterable=tasks or [], maxlen=maxlen)

    def add_migration_task(self, migration_task):
        if migration_task not in self._unique_queued_tasks:
            self._unique_queued_tasks.add(migration_task)
            self.append(migration_task)
        else:
            raise AlreadyQueued('%s is already queued!' % migration_task)

    @staticmethod
    def read_from_disk(filename):
        with open(filename, 'r') as f:
            return pickle.load(f)

    def save_to_disk(self, filename):
        with open(filename, 'w') as f:
            pickle.dump(self, f)


def do_migration(queue):
    while True:
        try:
            task = queue.popleft()
        except IndexError:
            #quit worker, means all tasks are done
            break
        try:
            task.run()
        except Exception:
            raise
        else:
            if not (task.is_done() or task.is_failed()):
                #re-queue task for further processing
                queue.append(task)
            else:
                #last execution of run, to print final migration done/failed message
                task.run()


if __name__ == '__main__':
    #set-up logging
    logging.basicConfig(format='%(levelname)s: %(message)s')
    logger = logging.getLogger('dbs3-migration')
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.DEBUG)

    block_names = ['test1', 'test1', 'test2', 'test3', 'test4']
    migration_queue = DBS3MigrationQueue()

    for block in block_names:
        try:
            migration_queue.add_migration_task(MigrationTask(block_name=block,
                                                             migration_url='http://a.b.c', dbs_client=None))
        except AlreadyQueued as aq:
            logger.warning(aq.message)

    migration_queue.save_to_disk('test.pkl')
    del migration_queue
    new_migration_queue = DBS3MigrationQueue.read_from_disk('test.pkl')
    do_migration(new_migration_queue)
