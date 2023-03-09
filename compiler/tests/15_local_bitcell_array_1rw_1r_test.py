#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2023 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
import sys, os
import unittest
from testutils import *

import openram
from openram import debug
from openram.sram_factory import factory
from openram import OPTS


# @unittest.skip("SKIPPING 05_local_bitcell_array_test")
class local_bitcell_array_1rw_1r_test(openram_test):

    def runTest(self):
        config_file = "{}/tests/configs/config".format(os.getenv("OPENRAM_HOME"))
        openram.init_openram(config_file, is_unit_test=True)

        OPTS.num_rw_ports = 1
        OPTS.num_r_ports = 1
        OPTS.num_w_ports = 0
        openram.setup_bitcell()

        debug.info(2, "Testing 4x4 local bitcell array for cell_1rw_1r without replica columns or dummy rows")
        a = factory.create(module_type="local_bitcell_array", cols=4, rows=4, rbl=[0, 0])
        self.local_check(a)

        debug.info(2, "Testing 4x4 local bitcell array for cell_1rw_1r without replica column but with dummy rows")
        a = factory.create(module_type="local_bitcell_array", cols=4, rows=4, rbl=[1, 1])
        self.local_check(a)

        debug.info(2, "Testing 4x4 local bitcell array for cell_1rw_1r with left replica column and dummy rows")
        a = factory.create(module_type="local_bitcell_array", cols=4, rows=4, rbl=[1, 1], left_rbl=[0])
        self.local_check(a)

        debug.info(2, "Testing 4x4 local bitcell array for cell_1rw_1r with right replica column and dummy rows")
        a = factory.create(module_type="local_bitcell_array", cols=4, rows=4, rbl=[1, 1], right_rbl=[1])
        self.local_check(a)

        debug.info(2, "Testing 4x4 local bitcell array for cell_1rw_1r with both replica columns and dummy rows")
        a = factory.create(module_type="local_bitcell_array", cols=4, rows=4, rbl=[1, 1], left_rbl=[0], right_rbl=[1])
        self.local_check(a)

        openram.end_openram()


# run the test from the command line
if __name__ == "__main__":
    (OPTS, args) = openram.parse_args()
    del sys.argv[1:]
    header(__file__, OPTS.tech_name)
    unittest.main(testRunner=debugTestRunner())
