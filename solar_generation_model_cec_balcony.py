#!/usr/bin/env python3

from enum import Enum
import pandas as pd
import pvlib

class Orientation(Enum):
    NORTH = 0
    EAST = 90
    SOUTH = 180
    WEST = 270

# ---------------------------------------------------------------------------
# These panel and inverter models are close approximations to what might get
# used in balcony solar plants in Germany.
# ---------------------------------------------------------------------------
cec_modules = pvlib.pvsystem.retrieve_sam("CECMod")
cec_inverters = pvlib.pvsystem.retrieve_sam("cecinverter")
DEFAULT_PANEL_300W = cec_modules["Trina_Solar_TSM_300DEG5_II_"] 
DEFAULT_PANEL_385W = cec_modules["Trina_Solar_TSM_385DE14H_II_"] 
DEFAULT_INVERTER_600W = cec_inverters["Altenergy_Power_System_Inc___YC600__240V_"]

def simulate_pv_output(
        location_name,
        local_tz,
        latitude,
        longitude,
        altitude,
        pv_array_tilt,
        pv_array_azimuth,
        pv_panel_model,
        inverter_model,
        pv_panel_count
    ):
    print(f"Calculating PV output for {location_name} using CEC Balcony")
    print(f"Panel {pv_panel_model.name}\nInverter {inverter_model.name}")
    print(f"Tilt (degrees) {pv_array_tilt}\norientation (N,E,S,W) {pv_array_azimuth}")
    
    # Fetch TMY (typical meteorological) "solar weather" data for the chosen location
    # "Solar weather" is how much sun we got at this location
    # https://pvlib-python.readthedocs.io/en/stable/reference/iotools.html
    solar_weather_timeseries, solar_weather_metadata = pvlib.iotools.get_pvgis_tmy(
        latitude=latitude,
        longitude=longitude,
        map_variables=True
    )
    # pvlib recommends providing timezone info at the beginning of a calculation
    # rather than localizing/converting at the end. The PVGIS TMY index is UTC,
    # so we convert it to local time immediately before any calculations.
    solar_weather_timeseries.index = solar_weather_timeseries.index.tz_convert(local_tz)
    solar_weather_timeseries.index.name = "time_local"
    #print(solar_weather_timeseries)

    # Fetch solar position relative to the location over the course of the simulation year
    solar_position_timeseries = pvlib.solarposition.get_solarposition(
        time=solar_weather_timeseries.index,
        latitude=latitude,
        longitude=longitude,
        altitude=altitude,
        temperature=solar_weather_timeseries["temp_air"],
        pressure=solar_weather_timeseries["pressure"]
    )

    # Get Plane of Array (POA) irradiance, i.e. the total sunlight - direct, diffuse and reflected
    # htting a solar panel's surface based on its specific tilt and orientation
    total_irradiance_timeseries = pvlib.irradiance.get_total_irradiance(
        pv_array_tilt, 
        pv_array_azimuth,
        solar_position_timeseries["apparent_zenith"], 
        solar_position_timeseries["azimuth"],
        solar_weather_timeseries["dni"], 
        solar_weather_timeseries["ghi"], 
        solar_weather_timeseries["dhi"],
        dni_extra=pvlib.irradiance.get_extra_radiation(solar_weather_timeseries.index),
        model="haydavies"
    )

    # Angle of incidence is the angle of the sun's rays relative to the panel's surface
    angle_of_incidence_timeseries = pvlib.irradiance.aoi(
        pv_array_tilt,
        pv_array_azimuth,
        solar_position_timeseries["apparent_zenith"],
        solar_position_timeseries["azimuth"],
    )

    angle_of_incidence_modifier = pvlib.iam.physical(angle_of_incidence_timeseries)
    effective_irradiance_timeseries = (
        total_irradiance_timeseries["poa_direct"] * angle_of_incidence_modifier 
        + total_irradiance_timeseries["poa_diffuse"])

    # Calculate cell temperature using an empirical heat loss factor model as implemented in PVsyst.
    # Mounting for balcony is assumed to be "insulated"
    cell_temperature_timeseries = pvlib.temperature.pvsyst_cell(
        total_irradiance_timeseries["poa_global"], 
        solar_weather_timeseries["temp_air"], 
        solar_weather_timeseries["wind_speed"],
        **pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS['pvsyst']['insulated']
    )

    # Get the DC Output using the database keys associated with the panel
    diode_params = pvlib.pvsystem.calcparams_cec(
        effective_irradiance=effective_irradiance_timeseries,
        temp_cell=cell_temperature_timeseries,
        a_ref=pv_panel_model['a_ref'],
        I_L_ref=pv_panel_model['I_L_ref'],
        I_o_ref=pv_panel_model['I_o_ref'],
        R_sh_ref=pv_panel_model['R_sh_ref'],
        R_s=pv_panel_model['R_s'],
        Adjust=pv_panel_model['Adjust'],
        alpha_sc=pv_panel_model['alpha_sc']
    )
    dc_electricity_timeseries = pvlib.pvsystem.singlediode(*diode_params)

    # Get the AC Output
    ac_electricity_timeseries_watts = pvlib.inverter.sandia(
        dc_electricity_timeseries["v_mp"], 
        dc_electricity_timeseries["p_mp"], 
        inverter_model
    )
    ac_electricity_timeseries_watts = ac_electricity_timeseries_watts.clip(lower=0)

    # Construct the dataframe
    pv_model_results_df = pd.DataFrame({
        "Inverter Output (Wh)": ac_electricity_timeseries_watts * pv_panel_count,
        "PV Array DC Output (Wh)": dc_electricity_timeseries["p_mp"] * pv_panel_count,
        "Solar azimuth (°)": solar_position_timeseries["azimuth"],
        "Solar elevation (°)": solar_position_timeseries["apparent_elevation"],
    }, index=solar_weather_timeseries.index).reset_index()

    annual_energy_kWh = (ac_electricity_timeseries_watts.sum() * pv_panel_count) / 1000
    print(f"Annual energy (kWh) = {annual_energy_kWh}")
    return {
        "results_df": pv_model_results_df,
        "annual_energy_kWh": annual_energy_kWh
    }

def test_get_pv_output():
    config = {
        'location_name': 'Berlin', 
        'local_tz': "Europe/Berlin",
        'latitude': 52.52, 
        'longitude': 13.40, 
        'altitude': 34,
        'pv_array_tilt': 90, # Vertical balcony
        'pv_array_azimuth': Orientation.SOUTH.value,
        'pv_panel_model': DEFAULT_PANEL_300W,
        'inverter_model': DEFAULT_INVERTER_600W,
        'pv_panel_count': 2
    }

    results = simulate_pv_output(**config)
    print(results["results_df"])
    print(f"\nTotal Annual Energy: {results['annual_energy_kWh']:.2f} kWh")

if __name__ == "__main__":
    test_get_pv_output()
