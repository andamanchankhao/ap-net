"""
Deprecated: superseded by run_demo_queue.py.

This file used to be a third copy of the demo runner, re-implementing the edge pipeline
and the ARQ protocol in-process (FIX_PLAN.md D2). run_demo_queue.py now does the same job
over in-memory queues while driving the real lora_protocol / receiver_runtime modules, so
there is nothing left for this script to do differently.

Kept as a shim so existing notes and shell history keep working.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    print("run_demo_sandboxed.py is deprecated - delegating to run_demo_queue.py\n")
    import run_demo_queue
    sys.exit(run_demo_queue.main())
