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
# used in rooftop solar systems in Pakistan.
# ---------------------------------------------------------------------------
cec_modules = pvlib.pvsystem.retrieve_sam("CECMod")
cec_inverters = pvlib.pvsystem.retrieve_sam("cecinverter")
DEFAULT_PANEL_MODEL = cec_modules["Jinko_Solar_Co___Ltd_JKM410M_72HL"]  # Jinko JKM410M-72HL (410W, Vmp=42.3V)
DEFAULT_INVERTER_MODEL_10kWSYSTEM = cec_inverters["Huawei_Technologies_Co___Ltd___SUN2000_10KTL_USL0__240V_"]       # example: 10kW
DEFAULT_INVERTER_MODEL_6kWSYSTEM = cec_inverters["Ningbo_Ginlong_Technologies_Co___Ltd___Solis_1P6K2_4G_US__240V_"] # example: 6kW
DEFAULT_INVERTER_MODEL_3kWSYSTEM = cec_inverters["Ningbo_Ginlong_Technologies_Co___Ltd___Solis_1P5K_4G_US__240V_"]  # example 5kW AC — smallest available Solis in CEC DB
# 10kW
#   9 panels/string → Vmp = 9×42.3 = 381V (Huawei MPPT 370-420V)
#   3 strings × 9 = 27 panels × 410W = 11.07 kW DC → ~10 kW AC (DC/AC = 1.11)
DEFAULT_PANELS_PER_STRING_10kWSYSTEM = 9
DEFAULT_STRINGS_IN_PARALLEL_10kWSYSTEM = 3
#  6kW
#   8 panels/string → Vmp = 8×42.3 = 338V (Solis MPPT 300-500V)
#.  2 strings × 8 = 16 panels × 410W = 6.56 kW DC → ~6 kW AC (DC/AC = 1.09)
DEFAULT_PANELS_PER_STRING_6kWSYSTEM = 8
DEFAULT_STRINGS_IN_PARALLEL_6kWSYSTEM = 2
# ~3kW DC
#   7 panels/string → Vmp = 7×42.3 = 296.1V (Solis 1P5K MPPT 240-500V)
#   1 string × 7 = 7 panels × 410W = 2.87 kW DC → ≤2.87 kW AC (inverter is 5kW, DC/AC=0.57)
#   This is a bit of an artificial example because no 3kW inverter exists in CEC DB for Pakistani 
#   brands; 1P5K is the smallest proxy. The inverter will never clip at this DC size.
DEFAULT_PANELS_PER_STRING_3kWSYSTEM = 7
DEFAULT_STRINGS_IN_PARALLEL_3kWSYSTEM = 1

def load_karachi_weather_csv(csv_path, latitude=24.86, longitude=67.01):
    """
    Load a Karachi weather CSV and return a pvlib-compatible solar_weather_timeseries.
    We use this function if we are working with the weather dataset published at 
    https://lei.lums.edu.pk/datasets/residential-energy-and-weather-data-pakistan.html
    Expected CSV columns:
        datetime, Temperature (°C), Wind Speed (km/h), Pressure (hPa),
        Solar Radiation (W/m²), [others ignored]
    Unit conversions applied:
        Wind Speed  km/h  → m/s  (÷ 3.6)
        Pressure    hPa   → Pa   (× 100)
    GHI → DNI + DHI decomposition via pvlib Erbs model.
    Rows with GHI < 0 (sensor noise) are clipped to 0.
    """
    df = pd.read_csv(csv_path)

    # Midnight rows have no time component ("2023-07-01"); hourly rows have 
    # "2023-07-01 01:00:00". format='mixed' handles both in one pass.
    df = df.set_index("datetime")
    df.index = pd.to_datetime(df.index, format="mixed").tz_localize("Asia/Karachi")
    df.index.name = "time_local"

    weather = pd.DataFrame(index=df.index)
    weather["temp_air"] = df["Temperature"].astype(float)
    weather["wind_speed"] = df["Wind Speed"].astype(float) / 3.6 # km/h → m/s
    weather["pressure"] = df["Pressure"].astype(float) * 100 # hPa  → Pa
    weather["ghi"] = df["Solar Radiation"].astype(float).clip(lower=0)

    # Compute solar zenith for the Erbs decomposition
    solar_pos = pvlib.solarposition.get_solarposition(
        time=weather.index,
        latitude=latitude,
        longitude=longitude
    )

    decomposed = pvlib.irradiance.erbs(
        ghi=weather["ghi"],
        zenith=solar_pos["apparent_zenith"],
        datetime_or_doy=weather.index
    )
    weather["dni"] = decomposed["dni"].clip(lower=0)
    weather["dhi"] = decomposed["dhi"].clip(lower=0)

    return weather


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
        panels_per_string,      # panels wired in series in each string
        strings_in_parallel,    # strings wired in parallel into the inverter
        weather_csv_path=None   # if provided, use observed data instead of PVGIS TMY
    ):
    pv_panel_count = panels_per_string * strings_in_parallel
    print(f"Calculating PV output for {location_name} using CEC Rooftop")
    print(f"{pv_panel_count} panels ({strings_in_parallel} string(s) × {panels_per_string} series)")
    print(f"Panel: {pv_panel_model.name}\nInverter: {inverter_model.name}")
    print(f"Tilt (degrees) {pv_array_tilt}\norientation (N,E,S,W) {pv_array_azimuth}")
    
    # Fetch historical "solar weather" data
    if weather_csv_path is not None:
        # Use observed 2023-2024 weather data (preferred when matching household load data)
        solar_weather_timeseries = load_karachi_weather_csv(
            weather_csv_path, latitude=latitude, longitude=longitude
        )
    else:
        # Fall back to PVGIS TMY (statistical typical-meteorological-year)
        # https://pvlib-python.readthedocs.io/en/stable/reference/iotools.html
        solar_weather_timeseries, _ = pvlib.iotools.get_pvgis_tmy(
            latitude=latitude,
            longitude=longitude,
            map_variables=True
        )
        # PVGIS TMY index is UTC; convert to local time before calculations
        solar_weather_timeseries.index = solar_weather_timeseries.index.tz_convert(local_tz)
        solar_weather_timeseries.index.name = "time_local"


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
    # Rack mounted rooftop could be considered "freestanding" instead of "insulated". Let's actually
    # pick the latter to be a bit conservative (i.e. overestimate cell temperature) to account for 
    # Pakistan's extreme summer heat ...
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
        R_s=pv_panel_model['R_s'],     # Matches your 'R_s'
        Adjust=pv_panel_model['Adjust'],
        alpha_sc=pv_panel_model['alpha_sc']
    )

    dc_electricity_timeseries = pvlib.pvsystem.singlediode(*diode_params)

    # Get the AC Output
    # For a string inverter the model needs the actual DC bus voltage and
    # total array power, not single-panel values.
    #   string_v_mp = single-panel Vmp × panels in series (sets DC bus voltage)
    #   array_p_mp = single-panel Pmp × total panel count (total DC watts in)
    string_v_mp = dc_electricity_timeseries["v_mp"] * panels_per_string
    array_p_mp  = dc_electricity_timeseries["p_mp"] * pv_panel_count

    ac_electricity_timeseries_watts = pvlib.inverter.sandia(
        v_dc=string_v_mp,
        p_dc=array_p_mp,
        inverter=inverter_model
    )
    ac_electricity_timeseries_watts = ac_electricity_timeseries_watts.clip(lower=0)

    # Construct the dataframe
    # ac_electricity_timeseries_watts is already the full array AC output so no need
    # to multiply by pv_panel_count.
    pv_model_results_df = pd.DataFrame({
        "Inverter Output (Wh)": ac_electricity_timeseries_watts,
        "PV Array DC Output (Wh)": array_p_mp,
        "Solar azimuth (°)": solar_position_timeseries["azimuth"],
        "Solar elevation (°)": solar_position_timeseries["apparent_elevation"],
    }, index=solar_weather_timeseries.index).reset_index()

    annual_energy_kWh = ac_electricity_timeseries_watts.sum() / 1000
    print(f"Annual energy (kWh) = {annual_energy_kWh}")
    return {
        "results_df": pv_model_results_df,
        "annual_energy_kWh": annual_energy_kWh
    }

def test_get_pv_output():

    config = {
        'location_name': 'Karachi',
        'local_tz': "Asia/Karachi",
        'latitude': 24.86,
        'longitude': 67.01,
        'altitude': 8,           
        'pv_array_tilt': 19,
        'pv_array_azimuth': Orientation.SOUTH.value,
        'pv_panel_model': DEFAULT_PANEL_MODEL,
        'inverter_model': DEFAULT_INVERTER_MODEL_3kWSYSTEM, 
        'panels_per_string': DEFAULT_PANELS_PER_STRING_3kWSYSTEM,
        'strings_in_parallel': DEFAULT_STRINGS_IN_PARALLEL_3kWSYSTEM,
        #'weather_csv_path': 'data/karachi_weather_2023_2024.csv',  # observed data
    }

    results = simulate_pv_output(**config)
    print(results["results_df"].head())
    print(f"\nTotal Annual Energy: {results['annual_energy_kWh']:.2f} kWh")

if __name__ == "__main__":
    test_get_pv_output()

