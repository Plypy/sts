#!/usr/bin/python

from debugger.debugger import FuzzTester
from debugger.deferred_io import DeferredIOWorker
import debugger.topology_generator as default_topology
from pox.lib.ioworker.io_worker import RecocoIOLoop
from debugger.experiment_config_lib import Controller
from pox.lib.recoco.recoco import Scheduler

import signal
import sys
import string
import subprocess
import time
import argparse
import logging
logging.basicConfig(level=logging.DEBUG)

# We use python as our DSL for specifying experiment configuration  
# The module can define the following functions:
#   controllers(command_line_args=[]) => returns a list of pox.debugger.experiment_config_info.ControllerInfo objects
#   switches()                        => returns a list of pox.debugger.experiment_config_info.Switch objects

description = """
Run a debugger experiment.
Example usage:

$ %s ./pox/pox.py --no-cli openflow.of_01 --address=__address__ --port=__port__
""" % (sys.argv[0])



parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
             description=description)
parser.add_argument("-n", "--non-interactive", help='run debugger non-interactively',
                    action="store_false", dest="interactive", default=True)
parser.add_argument("-c", "--config", help='optional experiment config file to load')
parser.add_argument('controller_args', metavar='controller arg', nargs=argparse.REMAINDER,
                   help='arguments to pass to the controller(s)')
#parser.disable_interspersed_args()
args = parser.parse_args()

if args.config:
  config = __import__(args.config_file)
else:
  config = object()

if hasattr(config, 'controllers'):
  controllers = config.controllers(args.controller_args)
else:
  controllers = [Controller(args.controller_args)]

child_processes = []
scheduler = None
def kill_children(kill=None):
  global child_processes
  global scheduler

  if kill == None:
    if hasattr(kill_children,"already_run"):
      kill = True
    else:
      kill = False
      kill_children.already_run = True

  if len(child_processes) == 0:
    return

  print >> sys.stderr, "%s child controllers..." % ("Killing" if kill else "Terminating"),
  for child in child_processes:
    if kill:
      child.kill()
    else:
      child.terminate()

  start_time = time.time()
  last_dot = start_time
  while True:
    for child in child_processes:
      if child.poll() != None:
        if child in child_processes:
          child_processes.remove(child)
    if len(child_processes) == 0:
      break
    time.sleep(0.1)
    now = time.time()
    if (now - last_dot) > 1:
      sys.stderr.write(".")
      last_dot = now
    if (now - start_time) > 5:
      if kill:
        break
      else:
        sys.stderr.write(' FAILED (timeout)!\n')
        return kill_children(kill=True)
  sys.stderr.write(' OK\n')

def kill_scheduler():
  if scheduler and not scheduler._hasQuit:
    sys.stderr.write("Stopping Recoco Scheduler...")
    scheduler.quit()
    sys.stderr.write(" OK\n")

def handle_int(signal, frame):
  print >> sys.stderr, "Caught signal %d, stopping sdndebug" % signal
  kill_children()
  kill_scheduler()
  sys.exit(0)

signal.signal(signal.SIGINT, handle_int)
signal.signal(signal.SIGTERM, handle_int)

try:
  # Boot the controllers
  for c in controllers:
    command_line_args = map(lambda(x): string.replace(x, "__port__", str(c.port)),
                        map(lambda(x): string.replace(x, "__address__", str(c.address)), c.cmdline))
    print command_line_args
    child = subprocess.Popen(command_line_args)
    child_processes.append(child)

  io_loop = RecocoIOLoop()

  #if hasattr(config, 'switches'):
  #  switches = config.switches()
  #else:
  #  switches = []
  # HACK
  create_worker = lambda(socket): DeferredIOWorker(io_loop.create_worker_for_socket(socket))

  (panel, switch_impls) = default_topology.populate(controllers,
                                                     create_worker,
                                                     num_switches=2)

  scheduler = Scheduler(daemon=True)
  scheduler.schedule(io_loop)

  # TODO: allow user to configure the fuzzer parameters, e.g. drop rate
  debugger = FuzzTester(args.interactive)
  debugger.start(panel, switch_impls)
finally:
  kill_children()
  kill_scheduler()
