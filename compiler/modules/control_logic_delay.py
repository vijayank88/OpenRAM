# See LICENSE for licensing information.
#
# Copyright (c) 2016-2021 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
import design
import debug
from sram_factory import factory
import math
from vector import vector
from globals import OPTS
import logical_effort


class control_logic_delay(design.design):
    """
    Dynamically generated Control logic for the total SRAM circuit.
    Variant: delay-based
    """

    def __init__(self, num_rows, words_per_row, word_size, spare_columns=None, sram=None, port_type="rw", name=""):
        """ Constructor """
        name = "control_logic_" + port_type
        super().__init__(name)
        debug.info(1, "Creating {}".format(name))
        self.add_comment("num_rows: {0}".format(num_rows))
        self.add_comment("words_per_row: {0}".format(words_per_row))
        self.add_comment("word_size {0}".format(word_size))

        self.sram=sram
        self.num_rows = num_rows
        self.words_per_row = words_per_row
        self.word_size = word_size
        self.port_type = port_type

        if not spare_columns:
            self.num_spare_cols = 0
        else:
            self.num_spare_cols = spare_columns

        self.num_cols = word_size * words_per_row + self.num_spare_cols
        self.num_words = num_rows * words_per_row

        self.enable_delay_chain_resizing = False
        self.inv_parasitic_delay = logical_effort.logical_effort.pinv

        # Determines how much larger the sen delay should be. Accounts for possible error in model.
        # FIXME: This should be made a parameter
        self.wl_timing_tolerance = 1
        self.wl_stage_efforts = None
        self.sen_stage_efforts = None

        if self.port_type == "rw":
            self.num_control_signals = 2
        else:
            self.num_control_signals = 1

        self.create_netlist()
        if not OPTS.netlist_only:
            self.create_layout()

    def create_netlist(self):
        self.setup_signal_busses()
        self.add_pins()
        self.add_modules()
        self.create_instances()

    def create_layout(self):
        """ Create layout and route between modules """
        self.place_instances()
        self.route_all()
        # self.add_lvs_correspondence_points()
        self.add_boundary()
        self.DRC_LVS()

    def add_pins(self):
        """ Add the pins to the control logic module. """
        self.add_pin_list(self.input_list + ["clk"], "INPUT")
        self.add_pin_list(self.output_list, "OUTPUT")
        self.add_pin("vdd", "POWER")
        self.add_pin("gnd", "GROUND")

    def add_modules(self):
        """ Add all the required modules """

        self.dff = factory.create(module_type="dff_buf")
        dff_height = self.dff.height

        self.ctrl_dff_array = factory.create(module_type="dff_buf_array",
                                             rows=self.num_control_signals,
                                             columns=1)

        self.and2 = factory.create(module_type="pand2",
                                   size=12,
                                   height=dff_height)

        # clk_buf drives a flop for every address
        addr_flops = math.log(self.num_words, 2) + math.log(self.words_per_row, 2)
        # plus data flops and control flops
        num_flops = addr_flops + self.word_size + self.num_spare_cols + self.num_control_signals
        # each flop internally has a FO 5 approximately
        # plus about 5 fanouts for the control logic
        clock_fanout = 5 * num_flops + 5
        self.clk_buf_driver = factory.create(module_type="pdriver",
                                             fanout=clock_fanout,
                                             height=dff_height)

        # We will use the maximum since this same value is used to size the wl_en
        # and the p_en_bar drivers
        # max_fanout = max(self.num_rows, self.num_cols)

        # wl_en drives every row in the bank
        # this calculation is from the rbl control logic, it may not be optimal in this circuit
        size_list = [max(int(self.num_rows / 9), 1), max(int(self.num_rows / 3), 1)]
        self.wl_en_driver = factory.create(module_type="pdriver",
                                           size_list=size_list,
                                           height=dff_height)

        # wl_en_unbuf is the weak timing signal that feeds wl_en_driver
        self.wl_en_and = factory.create(module_type="pand2",
                                        size=1, 
                                        height=dff_height)

        # w_en drives every write driver
        self.wen_and = factory.create(module_type="pand3",
                                      size=self.word_size + 8,
                                      height=dff_height)

        # s_en drives every sense amp
        self.sen_and3 = factory.create(module_type="pand3",
                                       size=self.word_size + self.num_spare_cols,
                                       height=dff_height)

        # used to generate inverted signals with low fanout
        self.inv = factory.create(module_type="pinv",
                                  size=1,
                                  height=dff_height)

        # p_en_bar drives every column in the bitcell array
        # but it is sized the same as the wl_en driver with
        # prepended 3 inverter stages to guarantee it is slower and odd polarity
        self.p_en_bar_driver = factory.create(module_type="pdriver",
                                              fanout=self.num_cols,
                                              height=dff_height)

        self.nand2 = factory.create(module_type="pnand2",
                                    height=dff_height)

        debug.check(OPTS.delay_chain_stages % 2,
                    "Must use odd number of delay chain stages for inverting delay chain.")
        self.multi_delay_chain=factory.create(module_type = "multi_delay_chain",
                                              fanout_list = 29 * [ OPTS.delay_chain_fanout_per_stage ], # TODO: generate this programatically
                                              pinout_list = [2, 12, 13, 15, 29]) # TODO: generate this list programatically

    # not being used
    def get_dynamic_delay_chain_size(self, previous_stages, previous_fanout):
        """Determine the size of the delay chain used for the Sense Amp Enable using path delays"""
        from math import ceil
        previous_delay_chain_delay = (previous_fanout + 1 + self.inv_parasitic_delay) * previous_stages
        debug.info(2, "Previous delay chain produced {} delay units".format(previous_delay_chain_delay))

        # This can be anything >=2
        delay_fanout = 3
        # The delay chain uses minimum sized inverters. There are (fanout+1)*stages inverters and each
        # inverter adds 1 unit of delay (due to minimum size). This also depends on the pinv value
        required_delay = self.wl_delay * self.wl_timing_tolerance - (self.sen_delay - previous_delay_chain_delay)
        debug.check(required_delay > 0, "Cannot size delay chain to have negative delay")
        delay_per_stage = delay_fanout + 1 + self.inv_parasitic_delay
        delay_stages = ceil(required_delay / delay_per_stage)
        # force an even number of stages.
        if delay_stages % 2 == 1:
            delay_stages += 1
            # Fanout can be varied as well but is a little more complicated but potentially optimal.
        debug.info(1, "Setting delay chain to {} stages with {} fanout to match {} delay".format(delay_stages, delay_fanout, required_delay))
        return (delay_stages, delay_fanout)

    # not being used
    def get_dynamic_delay_fanout_list(self, previous_stages, previous_fanout):
        """Determine the size of the delay chain used for the Sense Amp Enable using path delays"""

        previous_delay_per_stage = previous_fanout + 1 + self.inv_parasitic_delay
        previous_delay_chain_delay = previous_delay_per_stage * previous_stages
        debug.info(2, "Previous delay chain produced {} delay units".format(previous_delay_chain_delay))

        fanout_rise = fanout_fall = 2 # This can be anything >=2
        # The delay chain uses minimum sized inverters. There are (fanout+1)*stages inverters and each
        # inverter adds 1 unit of delay (due to minimum size). This also depends on the pinv value
        required_delay_fall = self.wl_delay_fall * self.wl_timing_tolerance - \
                              (self.sen_delay_fall - previous_delay_chain_delay / 2)
        required_delay_rise = self.wl_delay_rise * self.wl_timing_tolerance - \
                              (self.sen_delay_rise - previous_delay_chain_delay / 2)
        debug.info(2,
                   "Required delays from chain: fall={}, rise={}".format(required_delay_fall,
                                                                         required_delay_rise))

        # If the fanout is different between rise/fall by this amount. Stage algorithm is made more pessimistic.
        WARNING_FANOUT_DIFF = 5
        stages_close = False
        # The stages need to be equal (or at least a even number of stages with matching rise/fall delays)
        while True:
            stages_fall = self.calculate_stages_with_fixed_fanout(required_delay_fall,
                                                                  fanout_fall)
            stages_rise = self.calculate_stages_with_fixed_fanout(required_delay_rise,
                                                                  fanout_rise)
            debug.info(1,
                       "Fall stages={}, rise stages={}".format(stages_fall,
                                                               stages_rise))
            if abs(stages_fall - stages_rise) == 1 and not stages_close:
                stages_close = True
                safe_fanout_rise = fanout_rise
                safe_fanout_fall = fanout_fall

            if stages_fall == stages_rise:
                break
            elif abs(stages_fall - stages_rise) == 1 and WARNING_FANOUT_DIFF < abs(fanout_fall - fanout_rise):
                debug.info(1, "Delay chain fanouts between stages are large. Making chain size larger for safety.")
                fanout_rise = safe_fanout_rise
                fanout_fall = safe_fanout_fall
                break
            # There should also be a condition to make sure the fanout does not get too large.
            # Otherwise, increase the fanout of delay with the most stages, calculate new stages
            elif stages_fall>stages_rise:
                fanout_fall+=1
            else:
                fanout_rise+=1

        total_stages = max(stages_fall, stages_rise) * 2
        debug.info(1, "New Delay chain: stages={}, fanout_rise={}, fanout_fall={}".format(total_stages, fanout_rise, fanout_fall))

        # Creates interleaved fanout list of rise/fall delays. Assumes fall is the first stage.
        stage_list = [fanout_fall if i % 2==0 else fanout_rise for i in range(total_stages)]
        return stage_list

    # only used by above unused function
    def calculate_stages_with_fixed_fanout(self, required_delay, fanout):
        from math import ceil
        # Delay being negative is not an error. It implies that any amount of stages would have a negative effect on the overall delay
        # 3 is the minimum delay per stage (with pinv=0).
        if required_delay <= 3 + self.inv_parasitic_delay:
            return 1
        delay_per_stage = fanout + 1 + self.inv_parasitic_delay
        delay_stages = ceil(required_delay / delay_per_stage)
        return delay_stages

    def setup_signal_busses(self):
        """ Setup bus names, determine the size of the busses etc """

        # List of input control signals
        if self.port_type == "rw":
            self.input_list = ["csb", "web"]
        else:
            self.input_list = ["csb"]

        if self.port_type == "rw":
            self.dff_output_list = ["cs_bar", "cs", "we_bar", "we"]
        else:
            self.dff_output_list = ["cs_bar", "cs"]

        # list of output control signals (for making a vertical bus)
        if self.port_type == "rw":
            self.internal_bus_list = ["glitch2", "glitch3", "delay1", "delay2", "delay3", "delay4", "delay5", "gated_clk_bar", "gated_clk_buf", "we", "we_bar", "clk_buf", "cs"]
        else:
            self.internal_bus_list = ["glitch2", "glitch3", "delay1", "delay2", "delay3", "delay4", "delay5", "gated_clk_bar", "gated_clk_buf", "clk_buf", "cs"]
        # leave space for the bus plus one extra space
        self.internal_bus_width = (len(self.internal_bus_list) + 1) * self.m2_pitch

        # Outputs to the bank
        if self.port_type == "rw":
            self.output_list = ["s_en", "w_en"]
        elif self.port_type == "r":
            self.output_list = ["s_en"]
        else:
            self.output_list = ["w_en"]
        self.output_list.append("p_en_bar")
        self.output_list.append("wl_en")
        self.output_list.append("clk_buf")

        self.supply_list = ["vdd", "gnd"]

    def route_rails(self):
        """ Add the input signal inverted tracks """
        height = self.control_logic_center.y - self.m2_pitch
        offset = vector(self.ctrl_dff_array.width, 0)

        self.input_bus = self.create_vertical_bus("m2",
                                                  offset,
                                                  self.internal_bus_list,
                                                  height)

    def create_instances(self):
        """ Create all the instances """
        self.create_dffs()
        self.create_clk_buf_row()
        self.create_gated_clk_bar_row()
        self.create_gated_clk_buf_row()
        self.create_delay()
        self.create_glitches()
        self.create_wlen_row()
        if (self.port_type == "rw") or (self.port_type == "w"):
            self.create_wen_row()
        if (self.port_type == "rw") or (self.port_type == "r"):
            self.create_sen_row()
        self.create_pen_row()

    def place_instances(self):
        """ Place all the instances """
        # Keep track of all right-most instances to determine row boundary
        # and add the vdd/gnd pins
        self.row_end_inst = []

        # Add the control flops on the left of the bus
        self.place_dffs()

        # All of the control logic is placed to the right of the DFFs and bus
        self.control_x_offset = self.ctrl_dff_array.width + self.internal_bus_width

        row = 0
        # Add the logic on the right of the bus
        self.place_clk_buf_row(row)
        row += 1
        self.place_gated_clk_bar_row(row)
        row += 1
        self.place_gated_clk_buf_row(row)
        row += 1
        if (self.port_type == "rw") or (self.port_type == "r"):
            self.place_sen_row(row)
            row += 1
        if (self.port_type == "rw") or (self.port_type == "w"):
            self.place_wen_row(row)
            row += 1
        self.place_pen_row(row)
        row += 1
        self.place_wlen_row(row)
        row += 1
        self.place_glitch2_row(row)
        row += 1
        self.place_glitch3_row(row)
        row += 1

        control_center_y = self.glitch3_nand_inst.uy() + self.m3_pitch

        # Delay chain always gets placed at row 4
        self.place_delay(4)
        height = self.delay_inst.uy()

        # This offset is used for placement of the control logic in the SRAM level.
        self.control_logic_center = vector(self.ctrl_dff_inst.rx(), control_center_y)

        # Extra pitch on top and right
        self.height = height + 2 * self.m1_pitch
        # Max of modules or logic rows
        self.width = max([inst.rx() for inst in self.row_end_inst])
        if (self.port_type == "rw") or (self.port_type == "r"): 
            # TODO: why not w ports here?
            self.width = max(self.delay_inst.rx(), self.width)
        self.width += self.m2_pitch

    def route_all(self):
        """ Routing between modules """
        self.route_rails()
        self.route_dffs()
        self.route_wlen()
        if (self.port_type == "rw") or (self.port_type == "w"):
            self.route_wen()
        if (self.port_type == "rw") or (self.port_type == "r"):
            self.route_sen()
        self.route_delay()
        self.route_pen()
        self.route_clk_buf()
        self.route_gated_clk_bar()
        self.route_gated_clk_buf()
        self.route_supply()

    def create_delay(self):
        """ Create the delay chain """
        self.delay_inst=self.add_inst(name="multi_delay_chain",
                                      mod=self.multi_delay_chain)
        self.connect_inst(["gated_clk_buf", "delay1", "delay2", "delay3", "delay4", "delay5", "vdd", "gnd"])

    def place_delay(self, row):
        """ Place the delay chain """
        debug.check(row % 2 == 0, "Must place delay chain at even row for supply alignment.")

        # It is flipped on X axis
        y_off = row * self.and2.height + self.multi_delay_chain.height

        # Add to the right of the control rows and routing channel
        offset = vector(0, y_off)
        self.delay_inst.place(offset, mirror="MX")

    def route_delay(self):
	delay_map = zip(["in", "delay1", "delay2", "delay3", "delay4", "delay5"], 
			["gated_clk_buf", "delay1", "delay2", "delay3", "delay4", "delay5"])
	
	slef.connect_vertical_bus(delay_map, self.delay_inst, self.input_bus)

    # glitch{1-3} are internal timing signals based on different in/out
    # points on the delay chain for adjustable start time and duration
    def create_glitches(self):
        self.glitch1_nand_inst = self.add_inst(name="nand2_glitch1",
                                               mod=self.nand2)
        self.connect_inst(["delay1", "delay3", "glitch1", "vdd", "gnd"])

        self.glitch2_nand_inst = self.add_inst(name="nand2_glitch2",
                                               mod=self.nand2)
        self.connect_inst(["gated_clk_buf", "delay4", "glitch2", "vdd", "gnd"])

        self.glitch3_nand_inst = self.add_inst(name="nand2_glitch3",
                                               mod=self.nand2)
        self.connect_inst(["delay2", "delay5", "glitch3", "vdd", "gnd"])

    # glitch1 is placed in place_pen_row()

    def place_glitch2_row(self, row):
        x_offset = self.control_x_offset

        x_offset = self.place_util(self.glitch2_nand_inst, x_offset, row)

        self.row_end_inst.append(self.glitch2_nand_inst)

    def place_glitch3_row(self, row):
        x_offset = self.control_x_offset

        x_offset = self.place_util(self.glitch3_nand_inst, x_offset, row)

        self.row_end_inst.append(self.glitch3_nand_inst)
    
    def route_glitches(self):
        glitch2_map = zip(["A", "B", "Z"], ["gated_clk_buf", "delay4", "glitch2"])

        self.connect_vertical_bus(glitch2_map, self.glitch2_nand_inst, self.input_bus)

        glitch3_map = zip(["A", "B", "Z"], ["delay2", "delay5", "glitch3"])

        self.connect_vertical_bus(glitch3_map, self.glitch3_nand_inst, self.input_bus)

    def create_clk_buf_row(self):
        """ Create the multistage and gated clock buffer  """
        self.clk_buf_inst = self.add_inst(name="clkbuf",
                                          mod=self.clk_buf_driver)
        self.connect_inst(["clk", "clk_buf", "vdd", "gnd"])

    def place_clk_buf_row(self, row):
        x_offset = self.control_x_offset

        x_offset = self.place_util(self.clk_buf_inst, x_offset, row)

        self.row_end_inst.append(self.clk_buf_inst)

    def route_clk_buf(self):
        clk_pin = self.clk_buf_inst.get_pin("A")
        clk_pos = clk_pin.center()
        self.add_layout_pin_rect_center(text="clk",
                                        layer="m2",
                                        offset=clk_pos)
        self.add_via_stack_center(from_layer=clk_pin.layer,
                                  to_layer="m2",
                                  offset=clk_pos)

        self.route_output_to_bus_jogged(self.clk_buf_inst,
                                        "clk_buf")
        self.connect_output(self.clk_buf_inst, "Z", "clk_buf")

    def create_gated_clk_bar_row(self):
        self.clk_bar_inst = self.add_inst(name="inv_clk_bar",
                                            mod=self.inv)
        self.connect_inst(["clk_buf", "clk_bar", "vdd", "gnd"])

        self.gated_clk_bar_inst = self.add_inst(name="and2_gated_clk_bar",
                                                mod=self.and2)
        self.connect_inst(["clk_bar", "cs", "gated_clk_bar", "vdd", "gnd"])

    def place_gated_clk_bar_row(self, row):
        x_offset = self.control_x_offset

        x_offset = self.place_util(self.clk_bar_inst, x_offset, row)
        x_offset = self.place_util(self.gated_clk_bar_inst, x_offset, row)

        self.row_end_inst.append(self.gated_clk_bar_inst)

    def route_gated_clk_bar(self):
        clkbuf_map = zip(["A"], ["clk_buf"])
        self.connect_vertical_bus(clkbuf_map, self.clk_bar_inst, self.input_bus)

        out_pin = self.clk_bar_inst.get_pin("Z")
        out_pos = out_pin.center()
        in_pin = self.gated_clk_bar_inst.get_pin("A")
        in_pos = in_pin.center()
        self.add_zjog(out_pin.layer, out_pos, in_pos)
        self.add_via_stack_center(from_layer=out_pin.layer,
                                  to_layer=in_pin.layer,
                                  offset=in_pos)


        # This is the second gate over, so it needs to be on M3
        clkbuf_map = zip(["B"], ["cs"])
        self.connect_vertical_bus(clkbuf_map,
                                  self.gated_clk_bar_inst,
                                  self.input_bus,
                                  self.m2_stack[::-1])
        # The pin is on M1, so we need another via as well
        b_pin = self.gated_clk_bar_inst.get_pin("B")
        self.add_via_stack_center(from_layer=b_pin.layer,
                                  to_layer="m3",
                                  offset=b_pin.center())

        # This is the second gate over, so it needs to be on M3
        self.route_output_to_bus_jogged(self.gated_clk_bar_inst,
                                        "gated_clk_bar")

    def create_gated_clk_buf_row(self):
        self.gated_clk_buf_inst = self.add_inst(name="and2_gated_clk_buf",
                                                mod=self.and2)
        self.connect_inst(["clk_buf", "cs", "gated_clk_buf", "vdd", "gnd"])

    def place_gated_clk_buf_row(self, row):
        x_offset = self.control_x_offset

        x_offset = self.place_util(self.gated_clk_buf_inst, x_offset, row)

        self.row_end_inst.append(self.gated_clk_buf_inst)

    def route_gated_clk_buf(self):
        clkbuf_map = zip(["A", "B"], ["clk_buf", "cs"])
        self.connect_vertical_bus(clkbuf_map,
                                  self.gated_clk_buf_inst,
                                  self.input_bus)

        clkbuf_map = zip(["Z"], ["gated_clk_buf"])
        self.connect_vertical_bus(clkbuf_map,
                                  self.gated_clk_buf_inst,
                                  self.input_bus,
                                  self.m2_stack[::-1])
        # The pin is on M1, so we need another via as well
        z_pin = self.gated_clk_buf_inst.get_pin("Z")
        self.add_via_stack_center(from_layer=z_pin.layer,
                                  to_layer="m2",
                                  offset=z_pin.center())

    def create_wlen_row(self):
        self.wl_en_unbuf_and_inst = self.add_inst(name="and_wl_en_unbuf",
                                                  mod=self.wl_en_and)
        self.connect_inst(["cs", "glitch2", "wl_en_unbuf", "vdd", "gnd"])

        self.wl_en_driver_inst=self.add_inst(name="buf_wl_en",
                                      mod=self.wl_en_driver)
        self.connect_inst(["wl_en_unbuf", "wl_en", "vdd", "gnd"])

    def place_wlen_row(self, row):
        x_offset = self.control_x_offset

        x_offset = self.place_util(self.wl_en_unbuf_and_inst, x_offset, row)
        x_offset = self.place_util(self.wl_en_driver_inst, x_offset, row)

        self.row_end_inst.append(self.wl_en_driver_inst)

    def route_wlen(self):
        in_map = zip(["A", "B"], ["cs", "glitch2"])
        self.connect_vertical_bus(in_map, self.wl_en_unbuf_and_inst, self.input_bus)

        out_pin = self.wl_en_unbuf_and_inst.get_pin("Z")
        out_pos = out_pin.center()
        in_pin = self.p_en_bar_driver_inst.get_pin("A")
        in_pos = in_pin.center()
        mid1 = vector(in_pos.x, out_pos.y)
        self.add_path(out_pin.layer, [out_pos, mid1, in_pos])
        self.add_via_stack_center(from_layer=out_pin.layer,
                                  to_layer=in_pin.layer,
                                  offset=in_pin.center())
        self.connect_output(self.wl_en_driver_inst, "Z", "wl_en")

    def create_pen_row(self):
        self.p_en_bar_driver_inst=self.add_inst(name="buf_p_en_bar",
                                                mod=self.p_en_bar_driver)
        self.connect_inst(["glitch1", "p_en_bar", "vdd", "gnd"])

    def place_pen_row(self, row):
        x_offset = self.control_x_offset

        x_offset = self.place_util(self.glitch1_nand_inst, x_offset, row)
        x_offset = self.place_util(self.p_en_bar_driver_inst, x_offset, row)

        self.row_end_inst.append(self.p_en_bar_driver_inst)

    def route_pen(self):
        in_map = zip(["A", "B"], ["delay1", "delay3"])
        self.connect_vertical_bus(in_map, self.glitch1_nand_inst, self.input_bus)

        out_pin = self.glitch1_nand_inst.get_pin("Z") # same code here as wl_en, refactor?
        out_pos = out_pin.center()
        in_pin = self.p_en_bar_driver_inst.get_pin("A")
        in_pos = in_pin.center()
        mid1 = vector(in_pos.x, out_pos.y)
        self.add_path(out_pin.layer, [out_pos, mid1, in_pos])
        self.add_via_stack_center(from_layer=out_pin.layer,
                                  to_layer=in_pin.layer,
                                  offset=in_pin.center())

        self.connect_output(self.p_en_bar_driver_inst, "Z", "p_en_bar")

    def create_sen_row(self):
        if self.port_type=="rw":
            input_name = "we_bar"
        else:
            input_name = "cs"

        self.s_en_gate_inst = self.add_inst(name="and_s_en",
                                            mod=self.sen_and3)
        self.connect_inst(["glitch3", "gated_clk_bar", input_name, "s_en", "vdd", "gnd"])

    def place_sen_row(self, row):
        x_offset = self.control_x_offset

        x_offset = self.place_util(self.s_en_gate_inst, x_offset, row)

        self.row_end_inst.append(self.s_en_gate_inst)

    def route_sen(self):

        if self.port_type=="rw": # this is repeated many times in here, refactor?
            input_name = "we_bar"
        else:
            input_name = "cs"

        sen_map = zip(["A", "B", "C"], ["glitch3", "gated_clk_bar", input_name])
        self.connect_vertical_bus(sen_map, self.s_en_gate_inst, self.input_bus)

        self.connect_output(self.s_en_gate_inst, "Z", "s_en")

    def create_wen_row(self):
        self.glitch3_bar_inv_inst = self.add_inst(name="inv_glitch3_bar",
                                                  mod=self.inv)
        self.connect_inst(["glitch3", "glitch3_bar", "vdd", "gnd"])

        if self.port_type == "rw":
            input_name = "we"
        else:
            input_name = "cs"

        self.w_en_gate_inst = self.add_inst(name="and_w_en",
                                            mod=self.wen_and)
        self.connect_inst([input_name, "glitch2", "glitch3_bar", "w_en", "vdd", "gnd"])

    def place_wen_row(self, row):
        x_offset = self.control_x_offset

	x_offset = self.place_util(self.glitch3_bar_inv_inst, x_offset, row)
        x_offset = self.place_util(self.w_en_gate_inst, x_offset, row)

        self.row_end_inst.append(self.w_en_gate_inst)

    def route_wen(self): # w_en comes from a 3and but one of the inputs needs to be inverted, not sure if this implementation works.
        if self.port_type == "rw":
            input_name = "we"
        else:
            input_name = "cs"

        wen_map = zip(["A", "B"], [input_name, "glitch2"])
        self.connect_vertical_bus(wen_map, self.w_en_gate_inst, self.input_bus) # if there are problems, look here

        out_pin = self.glitch3_bar_inv_inst.get_pin("Z")
        out_pos = out_pin.center()
        in_pin = self.w_en_gate_inst.get_pin("C")
        in_pos = in_pin.center()
        mid1 = vector(in_pos.x, out_pos.y)
        self.add_path(out_pin.layer, [out_pos, mid1, in_pos])
        self.add_via_stack_center(from_layer=out_pin.layer,
                                  to_layer=in_pin.layer,
                                  offset=in_pin.center())

        self.connect_output(self.w_en_gate_inst, "Z", "w_en")

    def create_dffs(self):
        self.ctrl_dff_inst=self.add_inst(name="ctrl_dffs",
                                         mod=self.ctrl_dff_array)
        inst_pins = self.input_list + self.dff_output_list + ["clk_buf"] + self.supply_list
        self.connect_inst(inst_pins)

    def place_dffs(self):
        self.ctrl_dff_inst.place(vector(0, 0))

    def route_dffs(self):
        if self.port_type == "rw":
            dff_out_map = zip(["dout_bar_0", "dout_bar_1", "dout_1"], ["cs", "we", "we_bar"])
        elif self.port_type == "r":
            dff_out_map = zip(["dout_bar_0"], ["cs"])
        else:
            dff_out_map = zip(["dout_bar_0"], ["cs"])
        self.connect_vertical_bus(dff_out_map, self.ctrl_dff_inst, self.input_bus, self.m2_stack[::-1])

        # Connect the clock rail to the other clock rail
        # by routing in the supply rail track to avoid channel conflicts
        in_pos = self.ctrl_dff_inst.get_pin("clk").uc()
        mid_pos = vector(in_pos.x, self.gated_clk_buf_inst.get_pin("vdd").cy() - self.m1_pitch)
        rail_pos = vector(self.input_bus["clk_buf"].cx(), mid_pos.y)
        self.add_wire(self.m1_stack, [in_pos, mid_pos, rail_pos])
        self.add_via_center(layers=self.m1_stack,
                            offset=rail_pos)

        self.copy_layout_pin(self.ctrl_dff_inst, "din_0", "csb")
        if (self.port_type == "rw"):
            self.copy_layout_pin(self.ctrl_dff_inst, "din_1", "web")

    def get_offset(self, row):
        """ Compute the y-offset and mirroring """
        y_off = row * self.and2.height
        if row % 2:
            y_off += self.and2.height
            mirror="MX"
        else:
            mirror="R0"

        return (y_off, mirror)

    def connect_output(self, inst, pin_name, out_name):
        """ Create an output pin on the right side from the pin of a given instance. """

        out_pin = inst.get_pin(pin_name)
        out_pos = out_pin.center()
        right_pos = out_pos + vector(self.width - out_pin.cx(), 0)

        self.add_via_stack_center(from_layer=out_pin.layer,
                                  to_layer="m2",
                                  offset=out_pos)
        self.add_layout_pin_segment_center(text=out_name,
                                           layer="m2",
                                           start=out_pos,
                                           end=right_pos)

    def route_supply(self):
        """ Add vdd and gnd to the instance cells """

        supply_layer = self.dff.get_pin("vdd").layer

        max_row_x_loc = max([inst.rx() for inst in self.row_end_inst])
        for inst in self.row_end_inst:
            pins = inst.get_pins("vdd")
            for pin in pins:
                if pin.layer == supply_layer:
                    row_loc = pin.rc()
                    pin_loc = vector(max_row_x_loc, pin.rc().y)
                    self.add_power_pin("vdd", pin_loc, start_layer=pin.layer)
                    self.add_path(supply_layer, [row_loc, pin_loc])

            pins = inst.get_pins("gnd")
            for pin in pins:
                if pin.layer == supply_layer:
                    row_loc = pin.rc()
                    pin_loc = vector(max_row_x_loc, pin.rc().y)
                    self.add_power_pin("gnd", pin_loc, start_layer=pin.layer)
                    self.add_path(supply_layer, [row_loc, pin_loc])

        self.copy_layout_pin(self.delay_inst, "gnd")
        self.copy_layout_pin(self.delay_inst, "vdd")

        self.copy_layout_pin(self.ctrl_dff_inst, "gnd")
        self.copy_layout_pin(self.ctrl_dff_inst, "vdd")

    # not used
    def add_lvs_correspondence_points(self):
        """ This adds some points for easier debugging if LVS goes wrong.
        These should probably be turned off by default though, since extraction
        will show these as ports in the extracted netlist.
        """
        # pin=self.clk_inv1.get_pin("Z")
        # self.add_label_pin(text="clk1_bar",
        #                    layer="m1",
        #                    offset=pin.ll(),
        #                    height=pin.height(),
        #                    width=pin.width())

        # pin=self.clk_inv2.get_pin("Z")
        # self.add_label_pin(text="clk2",
        #                    layer="m1",
        #                    offset=pin.ll(),
        #                    height=pin.height(),
        #                    width=pin.width())

        pin=self.delay_inst.get_pin("out")
        self.add_label_pin(text="out",
                           layer=pin.layer,
                           offset=pin.ll(),
                           height=pin.height(),
                           width=pin.width())

    def graph_exclude_dffs(self):
        """Exclude dffs from graph as they do not represent critical path"""

        self.graph_inst_exclude.add(self.ctrl_dff_inst)
        if self.port_type=="rw" or self.port_type=="w":
            self.graph_inst_exclude.add(self.w_en_gate_inst)

    def place_util(self, inst, x_offset, row):
        """ Utility to place a row and compute the next offset """

        (y_offset, mirror) = self.get_offset(row)
        offset = vector(x_offset, y_offset)
        inst.place(offset, mirror)
        return x_offset + inst.width

    def route_output_to_bus_jogged(self, inst, name):
        # Connect this at the bottom of the buffer
        out_pin = inst.get_pin("Z")
        out_pos = out_pin.center()
        mid1 = vector(out_pos.x, out_pos.y - 0.4 * inst.mod.height)
        mid2 = vector(self.input_bus[name].cx(), mid1.y)
        bus_pos = self.input_bus[name].center()
        self.add_wire(self.m2_stack[::-1], [out_pos, mid1, mid2, bus_pos])
        self.add_via_stack_center(from_layer=out_pin.layer,
                                  to_layer="m2",
                                  offset=out_pos)

    def get_left_pins(self, name):
        """
        Return the left side supply pins to connect to a vertical stripe.
        """
        return(self.cntrl_dff_inst.get_pins(name) + self.delay_inst.get_pins(name))
