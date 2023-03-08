# See LICENSE for licensing information.
#
# Copyright (c) 2016-2023 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#

import datetime
from math import ceil, log, sqrt
from openram.base import vector
from openram.base import design
from openram import OPTS, debug, print_time
from openram.sram_factory import factory
from openram.tech import drc, layer, parameter

class rom_base_bank(design):

    """
    Rom data bank with row and column decoder + control logic

    word size is in bytes
    """

    def __init__(self, name, rom_config):
        super().__init__(name=name)
        self.rom_config = rom_config
        rom_config.set_local_config(self)

        self.word_size = self.word_bits
        # self.read_binary(word_size=word_size, data_file=data_file, scramble_bits=True, endian="little")
        # debug.info(1, "Rom data: {}".format(self.data))
        self.num_outputs = self.rows
        self.num_inputs = ceil(log(self.rows, 2))
        self.col_bits = ceil(log(self.words_per_row, 2))
        self.row_bits = self.num_inputs

        self.tap_spacing = self.strap_spacing

        try:
            from openram.tech import power_grid
            self.supply_stack = power_grid
        except ImportError:
            # if no power_grid is specified by tech we use sensible defaults
            # Route a M3/M4 grid
            self.supply_stack = self.m3_stack

        self.interconnect_layer = "m1"
        self.bitline_layer = "m1"
        self.wordline_layer = "m2"

        if "li" in layer:
            self.route_stack = self.m1_stack
        else:
            self.route_stack = self.m2_stack
        self.route_layer = self.route_stack[0]


    def create_netlist(self):
        start_time = datetime.datetime.now()
        self.add_modules()
        self.add_pins()
        self.create_instances()
        if not OPTS.is_unit_test:
            print_time("Submodules", datetime.datetime.now(), start_time)

    def create_layout(self):

        start_time = datetime.datetime.now()

        self.setup_layout_constants()
        self.place_instances()
        if not OPTS.is_unit_test:
            print_time("Placement", datetime.datetime.now(), start_time)


        start_time = datetime.datetime.now()
        self.route_layout()
        if not OPTS.is_unit_test:
            print_time("Routing", datetime.datetime.now(), start_time)

        self.height = self.array_inst.height
        self.width = self.array_inst.width
        self.add_boundary()

        start_time = datetime.datetime.now()
        if not OPTS.is_unit_test:
            # We only enable final verification if we have routed the design
            # Only run this if not a unit test, because unit test will also verify it.
            self.DRC_LVS(final_verification=OPTS.route_supplies, force_check=OPTS.check_lvsdrc)
            print_time("Verification", datetime.datetime.now(), start_time)

    def route_layout(self):
        self.route_decode_outputs()
        self.route_precharge()
        self.route_clock()
        self.route_array_outputs()
        self.place_top_level_pins()
        self.route_supplies()
        self.route_output_buffers()

    def setup_layout_constants(self):
        self.route_layer_width = drc["minwidth_{}".format(self.route_stack[0])]
        self.route_layer_pitch = drc["{0}_to_{0}".format(self.route_stack[0])]

        self.interconnect_layer_width = drc["minwidth_{}".format(self.interconnect_layer)]
        self.interconnect_layer_pitch = drc["{0}_to_{0}".format(self.interconnect_layer)]

    def add_pins(self):

        self.add_pin("clk", "INPUT")
        self.add_pin("CS", "INPUT")

        for i in range(self.row_bits + self.col_bits):
            self.add_pin("addr_{}".format(i), "INPUT")

        out_pins = []
        for j in range(self.word_size):
            out_pins.append("rom_out_{}".format(j))
        self.add_pin_list(out_pins, "OUTPUT")

        self.add_pin("vdd", "POWER")
        self.add_pin("gnd", "GROUND")


    def add_modules(self):

        # TODO: provide technology-specific calculation of these parameters
        # in sky130 the address control buffer is composed of 2 size 2 NAND gates,
        # with a beta of 3, each of these gates has gate capacitance of 2 min sized inverters, therefor a load of 4


        addr_control_buffer_effort = parameter['beta'] + 1
        # a single min sized nmos makes up 1/4 of the input capacitance of a min sized inverter
        bitcell_effort = 0.25

        # Takes into account inverter sizing
        wordline_effort = bitcell_effort * 0.5

        # a single min sized pmos plus a single min sized nmos have approximately half the gate capacitance of a min inverter
        # an additional 0.2 accounts for the long wire capacitance and add delay to gaurentee the read timing
        precharge_cell_effort = 0.5 + 0.2

        self.array = factory.create(module_type="rom_base_array",
                                    cols=self.cols,
                                    rows=self.rows,
                                    strap_spacing=self.strap_spacing,
                                    bitmap=self.data,
                                    bitline_layer=self.bitline_layer,
                                    wordline_layer=self.wordline_layer,
                                    pitch_match=True,
                                    tap_spacing=self.tap_spacing)


        self.decode_array = factory.create(module_name="rom_row_decode",
                                           module_type="rom_decoder",
                                           num_outputs=self.rows,
                                           strap_spacing=self.strap_spacing,
                                           route_layer=self.route_layer,
                                           fanout=(self.cols)*wordline_effort )


        self.column_mux = factory.create(module_type="rom_column_mux_array",
                                         columns=self.cols,
                                         word_size=self.word_size,
                                         tap_spacing=self.strap_spacing,
                                         bitline_layer=self.interconnect_layer,
                                         input_layer=self.bitline_layer)

        self.column_decode = factory.create(module_name="rom_column_decode",
                                            module_type="rom_decoder",
                                            num_outputs=self.words_per_row,
                                            strap_spacing=self.strap_spacing,
                                            route_layer=self.route_layer,
                                            fanout=2,
                                            invert_outputs=True )

        self.control_logic = factory.create(module_type="rom_control_logic",
                                            num_outputs=(self.cols + self.words_per_row * precharge_cell_effort) \
                                                         + (addr_control_buffer_effort * self.col_bits),
                                            clk_fanout=(self.row_bits * addr_control_buffer_effort) + (precharge_cell_effort * self.rows),
                                            height=self.column_decode.height )

        self.bitline_inv = factory.create(module_type="rom_wordline_driver_array",
                                            module_name="rom_bitline_inverter",
                                            rows=self.cols,
                                            fanout=4,
                                            invert_outputs=True,
                                            tap_spacing=0,
                                            flip_io=True)
        self.output_inv = factory.create(module_type="rom_wordline_driver_array",
                                            module_name="rom_output_buffer",
                                            rows=self.word_size,
                                            fanout=4,
                                            invert_outputs=True)


    def create_instances(self):
        gnd = ["gnd"]
        vdd = ["vdd"]
        prechrg = ["precharge"]
        clk = ["clk_int"]

        bitlines = ["bl_{}".format(bl) for bl in range(self.cols)]
        wordlines = ["wl_{}".format(wl) for wl in range(self.rows)]

        addr_msb = ["addr_{}".format(addr + self.col_bits) for addr in range(self.row_bits)]
        addr_lsb = ["addr_{}".format(addr) for addr in range(self.col_bits)]

        select_lines = ["word_sel_{}".format(word) for word in range(self.words_per_row)]

        bitline_bar = ["bl_b_{}".format(bl) for bl in range(self.cols)]
        pre_buf_outputs = ["rom_out_prebuf_{}".format(bit) for bit in range(self.word_size)]
        outputs = ["rom_out_{}".format(bl) for bl in range(self.word_size)]


        array_pins = bitlines + wordlines + prechrg + vdd + gnd

        row_decode_pins = addr_msb + wordlines + clk + clk + vdd + gnd
        col_decode_pins = addr_lsb + select_lines + prechrg + prechrg + vdd + gnd

        col_mux_pins = bitline_bar + select_lines + pre_buf_outputs + gnd

        bitline_inv_pins = bitlines + bitline_bar + vdd + gnd

        output_buf_pins = pre_buf_outputs + outputs + vdd + gnd

        self.array_inst = self.add_inst(name="rom_bit_array", mod=self.array)
        self.connect_inst(array_pins)

        self.decode_inst = self.add_inst(name="rom_row_decoder", mod=self.decode_array)
        self.connect_inst(row_decode_pins)

        self.control_inst = self.add_inst(name="rom_control", mod=self.control_logic)
        self.connect_inst(["clk", "CS", "precharge", "clk_int", "vdd", "gnd"])

        self.mux_inst = self.add_inst(name="rom_column_mux", mod=self.column_mux)
        self.connect_inst(col_mux_pins)

        self.col_decode_inst = self.add_inst(name="rom_column_decoder", mod=self.column_decode)
        self.connect_inst(col_decode_pins)

        self.bitline_inv_inst = self.add_inst(name="rom_bitline_inverter", mod=self.bitline_inv)
        self.connect_inst(bitline_inv_pins)

        self.output_inv_inst = self.add_inst(name="rom_output_inverter", mod=self.output_inv)
        self.connect_inst(output_buf_pins)




    def place_instances(self):
        self.place_row_decoder()
        self.place_data_array()
        self.place_bitline_inverter()
        self.place_col_mux()
        self.place_col_decoder()
        self.place_control_logic()
        self.place_output_buffer()


    def place_row_decoder(self):
        self.decode_offset = vector(0, self.control_inst.height )
        self.decode_inst.place(offset=self.decode_offset)

    def place_data_array(self):
        # We approximate the correct position for the array
        array_x = self.decode_inst.width + (2) * ( self.route_layer_width + self.route_layer_pitch )
        array_y = self.decode_array.buf_inst.height - self.array.precharge_inst.cy() - self.array.zero_cell.height * 0.5
        self.array_offset = vector(array_x ,array_y)
        self.array_inst.place(offset=self.array_offset)

        # now move array to correct alignment with decoder
        array_align = self.decode_inst.get_pin("wl_0").cy() - self.array_inst.get_pin("wl_0_0").cy()
        self.array_inst.place(offset=(self.array_offset + vector(0, array_align)))

    def place_bitline_inverter(self):
        self.bitline_inv_inst.place(offset=[0,0], rotate=90)
        inv_y_offset = self.array_inst.by() - self.bitline_inv_inst.width - 2 * self.m1_pitch

        inv_x_offset = self.array_inst.get_pin("bl_0_0").cx() - self.bitline_inv_inst.get_pin("out_0").cx()
        self.inv_offset = vector(inv_x_offset, inv_y_offset)
        self.bitline_inv_inst.place(offset=self.inv_offset, rotate=90)

    def place_control_logic(self):

        self.control_offset = vector(self.col_decode_inst.lx() - self.control_inst.width - 3 * self.m1_pitch, self.decode_inst.by() - self.control_logic.height - self.m1_pitch)
        self.control_inst.place(offset=self.control_offset)

    def place_col_decoder(self):
        col_decode_y = self.mux_inst.get_pin("sel_0").cy() - self.col_decode_inst.get_pin("wl_0").cy()
        self.col_decode_offset = vector(self.decode_inst.width - self.col_decode_inst.width, col_decode_y)
        self.col_decode_inst.place(offset=self.col_decode_offset)

    def place_col_mux(self):
        mux_y_offset = self.bitline_inv_inst.by() - self.mux_inst.height - 5 * self.route_layer_pitch

        mux_x_offset = self.bitline_inv_inst.get_pin("out_0").cx() - self.mux_inst.get_pin("bl_0").cx()
        self.mux_offset = vector(mux_x_offset, mux_y_offset)
        self.mux_inst.place(offset=self.mux_offset)

    def place_output_buffer(self):
        output_x = self.col_decode_inst.rx() + self.output_inv_inst.height
        output_y = self.mux_inst.by() - self.word_size * self.m1_pitch
        self.output_inv_offset = vector(output_x, output_y)
        self.output_inv_inst.place(offset=self.output_inv_offset, rotate=270)

    def route_decode_outputs(self):
        # for the row decoder
        route_pins = [self.array_inst.get_pin("wl_0_{}".format(wl)) for wl in range(self.rows)]
        decode_pins = [self.decode_inst.get_pin("wl_{}".format(wl)) for wl in range(self.rows)]
        route_pins.extend(decode_pins)
        self.connect_row_pins(self.interconnect_layer, route_pins, round=True)


        # then for the column decoder
        col_decode_pins = [self.col_decode_inst.get_pin("wl_{}".format(wl)) for wl in range(self.words_per_row)]
        sel_pins = [self.mux_inst.get_pin("sel_{}".format(wl)) for wl in range(self.words_per_row)]
        sel_pins.extend(col_decode_pins)
        self.connect_row_pins(self.wordline_layer, sel_pins, round=True)



    def route_array_inputs(self):

        for wl in range(self.rows):
            array_wl = self.array.wordline_names[0][wl]
            array_wl_pin = self.array_inst.get_pin(array_wl)

            wl_bus_wire = self.wl_bus[self.wl_interconnects[wl]]

            end = array_wl_pin.center()
            start = vector(wl_bus_wire.cx(), end.y)

            self.add_segment_center(self.interconnect_layer, start, end)


    def route_precharge(self):

        prechrg_control = self.control_inst.get_pin("prechrg")

        col_decode_prechrg = self.col_decode_inst.get_pin("precharge_r")
        col_decode_clk = self.col_decode_inst.get_pin("clk")
        array_prechrg = self.array_inst.get_pin("precharge")


        # Route precharge signal to the row decoder
        # end = vector(row_decode_prechrg.cx() - 0.5 * self.interconnect_layer_width, prechrg_control.cy())

        # self.add_segment_center(self.interconnect_layer, prechrg_control.center(), end)

        # start = end + vector(0.5 * self.interconnect_layer_width, 0)
        # self.add_segment_center(self.interconnect_layer, start, row_decode_prechrg.center())

        self.add_via_stack_center(from_layer=self.route_stack[0],
                                  to_layer=prechrg_control.layer,
                                  offset=prechrg_control.center())

        # Route precharge to col decoder
        start = prechrg_control.center()
        mid1 = vector(self.control_inst.rx(), prechrg_control.cy())
        mid2 = vector(self.control_inst.rx(), col_decode_prechrg.cy())
        end = col_decode_prechrg.center()
        self.add_path(self.route_stack[0], [start, mid1, mid2, end])

        self.add_via_stack_center(from_layer=self.route_stack[0],
                                  to_layer=col_decode_prechrg.layer,
                                  offset=end)

        start = mid1
        mid1 = vector(self.control_inst.rx(), start.y)
        mid2 = vector(mid1.x, col_decode_clk.cy())
        end = col_decode_clk.center()
        self.add_path(self.route_stack[0], [start, mid1, mid2, end])

        # self.add_segment_center(col_decode_prechrg.layer, end, col_decode_prechrg.center())

        # Route precharge to main array
        # end = vector(col_decode_prechrg.cx(), array_prechrg.cy())
        mid = vector(col_decode_prechrg.cx(), array_prechrg.cy() )
        self.add_path(self.route_stack[0], [array_prechrg.center(), mid, col_decode_prechrg.center()])


    def route_clock(self):
        clk_out = self.control_inst.get_pin("clk_out")
        row_decode_clk = self.decode_inst.get_pin("clk")

        self.add_via_stack_center(from_layer=self.route_stack[2],
                                  to_layer=clk_out.layer,
                                  offset=clk_out.center())

        # Route clock to row decoder
        mid = vector(self.control_inst.rx() + self.m1_pitch, clk_out.cy())

        addr_control_clk = row_decode_clk.rc() + vector( 2 * self.route_layer_pitch + self.route_layer_width, 0)
        row_decode_prechrg = self.decode_inst.get_pin("precharge")

        self.add_path(self.route_stack[2], [clk_out.center(), mid, addr_control_clk, row_decode_prechrg.center()])

        self.add_via_stack_center(from_layer=self.route_stack[2],
                                  to_layer=row_decode_clk.layer,
                                  offset=addr_control_clk)

        self.add_segment_center(row_decode_clk.layer, addr_control_clk, row_decode_clk.rc())

        # Route clock to column decoder
        # end = col_decode_clk.lc() - vector( 2 * self.route_layer_pitch + self.route_layer_width, 0)
        # self.add_path(self.route_stack[2], [clk_out.center(), end])

        # self.add_via_stack_center(from_layer=self.route_stack[2],
        #                           to_layer=row_decode_clk.layer,
        #                           offset=end)

        # self.add_segment_center(col_decode_clk.layer, end, col_decode_clk.lc())




    def route_array_outputs(self):
        array_out_pins = [self.array_inst.get_pin("bl_0_{}".format(bl)) for bl in range(self.cols)]
        inv_in_pins = [self.bitline_inv_inst.get_pin("in_{}".format(bl)) for bl in range(self.cols)]
        inv_out_pins = [self.bitline_inv_inst.get_pin("out_{}".format(bl)) for bl in range(self.cols)]
        mux_pins = [self.mux_inst.get_pin("bl_{}".format(bl)) for bl in range(self.cols)]

        self.connect_col_pins(self.interconnect_layer, array_out_pins + inv_in_pins, round=True, directions="nonpref")
        self.connect_col_pins(self.interconnect_layer, inv_out_pins + mux_pins, round=True, directions="nonpref")





    def route_output_buffers(self):
        mux = self.mux_inst
        buf = self.output_inv_inst
        route_nets = [ [mux.get_pin("bl_out_{}".format(bit)), buf.get_pin("in_{}".format(bit))] for bit in range(self.word_size)]

        channel_ll = vector( route_nets[0][0].cx(), route_nets[0][1].cy() + self.m1_pitch * 3)
        self.create_horizontal_channel_route(netlist=route_nets, offset=channel_ll, layer_stack=self.m1_stack)




    def place_top_level_pins(self):
        self.copy_layout_pin(self.control_inst, "CS")
        self.copy_layout_pin(self.control_inst, "clk_in", "clk")

        for i in range(self.word_size):
            self.copy_layout_pin(self.output_inv_inst, "out_{}".format(i), "rom_out_{}".format(i))
        for lsb in range(self.col_bits):
            name = "addr_{}".format(lsb)
            self.copy_layout_pin(self.col_decode_inst, "A{}".format(lsb), name)

        for msb in range(self.col_bits, self.row_bits + self.col_bits):
            name = "addr_{}".format(msb)
            pin_num = msb - self.col_bits
            self.copy_layout_pin(self.decode_inst, "A{}".format(pin_num), name)

    def route_supplies(self):

        for inst in self.insts:
            if not inst.mod.name.__contains__("contact"):
                self.copy_layout_pin(inst, "vdd")
                self.copy_layout_pin(inst, "gnd")

    # """
    # Reads a hexadecimal file from a given directory to be used as the data written to the ROM
    # endian is either "big" or "little"
    # word_size is the number of bytes per word
    # sets the row and column size based on the size of binary input, tries to keep array as square as possible,
    # """

    # def read_binary(self, data_file, word_size=2, endian="big", scramble_bits=False):
    #     # Read data as hexidecimal text file
    #     hex_file = open(data_file, 'r')
    #     hex_data = hex_file.read()

    #     # Convert from hex into an int
    #     data_int = int(hex_data, 16)
    #     # Then from int into a right aligned, zero padded string
    #     bin_string = bin(data_int)[2:].zfill(len(hex_data) * 4)

    #     # Then turn the string into a list of ints
    #     bin_data = list(bin_string)
    #     bin_data = [int(x) for x in bin_data]

    #     # data size in bytes
    #     data_size = len(bin_data) / 8
    #     num_words = int(data_size / word_size)

    #     bytes_per_col = sqrt(num_words)

    #     self.words_per_row = int(ceil(bytes_per_col /(2*word_size)))

    #     bits_per_row = self.words_per_row * word_size * 8

    #     self.cols = bits_per_row
    #     self.rows = int(num_words / (self.words_per_row))
    #     chunked_data = []

    #     for i in range(0, len(bin_data), bits_per_row):
    #         row_data = bin_data[i:i + bits_per_row]
    #         if len(row_data) < bits_per_row:
    #             row_data = [0] * (bits_per_row - len(row_data)) + row_data
    #         chunked_data.append(row_data)


    #     # if endian == "big":


    #     self.data = chunked_data
    #     if scramble_bits:
    #         scrambled_chunked = []

    #         for row_data in chunked_data:
    #             scambled_data = []
    #             for bit in range(self.word_size):
    #                 for word in range(self.words_per_row):
    #                     scambled_data.append(row_data[bit + word * self.word_size])
    #             scrambled_chunked.append(scambled_data)
    #         self.data = scrambled_chunked



    #     # self.data.reverse()

    #     debug.info(1, "Read rom binary: length {0} bytes, {1} words, set number of cols to {2}, rows to {3}, with {4} words per row".format(data_size, num_words, self.cols, self.rows, self.words_per_row))