import numpy as np
import pandapower as pp
from pandapower.plotting import pf_res_plotly

def check_types():
    net = pp.create_empty_network()
    print(pp.available_std_types(net, "line"))

def run_neighborhood_cluster():
    # Initialize a blank network
    net = pp.create_empty_network()

    # Add a Medium Voltage (MV) bus upstream of the Low Voltage (LV) feeder 
    # (10 kV to match pandapower std trafo types)
    bus_mv = pp.create_bus(net, vn_kv=10.0, name="MV Bus")

    # Connect ext_grid to the MV bus
    pp.create_ext_grid(net, bus=bus_mv, vm_pu=1.0)
 
    # Create the Transformer Bus (230V Phase-to-Neutral / 400V Phase-to-Phase)
    # and external grid model of the upstream network. In Pakistan the standard
    # for Phase-to-Neutral is 230V. 230 × √3 ~= 400V.
    bus_xfmr = pp.create_bus(net, vn_kv=0.4, name="Transformer")

    # Create a distribution transformer MV → LV
    # Smallest available std type is 250 kVA — typical Pakistani neighborhood transformer
    pp.create_transformer(net, hv_bus=bus_mv, lv_bus=bus_xfmr, std_type="0.25 MVA 10/0.4 kV")

    # Add 10 Houses along a 500m radial feeder
    prev_bus = bus_xfmr
    for i in range(1, 16):
        curr_bus = pp.create_bus(net, vn_kv=0.4, name=f"House {i}")

        # Use a typical overhead line cable type. ACSR (Aluminum Conductor Steel Reinforced) —
        # the aluminum carries current the steel core provides tensile strength for spans 
        # between poles. For a residential feeder "Al/St 70/11" — 70mm² aluminum with 11 mm2 
        # steel core is the typical size for a Pakistani LV feeder serving a cluster of 
        # houses (WAPDA/DISCO standard). Closest approximation that is available in this 
        # library of pandapower is the 48-AL1/8-ST1A 0.4
        pp.create_line(net,  
            from_bus=prev_bus, 
            to_bus=curr_bus, 
            length_km=0.05, 
            std_type="48-AL1/8-ST1A 0.4", 
            name=f"Line {i}")
        
        # Model peak solar noon on a summer day in a household that 
        # oversized their system and isn't actively powering the ACs
        # right now.
        # Add Load (1kW) and Solar (10kW) to each house
        # From dataset available, House 40 appliance range was 1.39kW to 8.89 kW
        # so this seems like a decent approximation for base load.
        pp.create_load(net, bus=curr_bus, p_mw=0.001, name=f"Load {i}")
        pp.create_sgen(net, bus=curr_bus, p_mw=0.010, name=f"Solar {i}")
        
        prev_bus = curr_bus

    # Run the Power Flow
    pp.runpp(net, numba=False)

    # Check the result (Voltage at House 10)
    print(net.res_bus.vm_pu * 230)  # Shows the voltage rise at each bus in Volts
    print(net.res_line)             # loading_percent — how loaded each line segment is
    print(net.res_trafo)

    # has dependency on igraph
    #fig = pf_res_plotly(net, figsize=1, auto_open=True)
    #return fig
    
if __name__ == "__main__":
    run_neighborhood_cluster()