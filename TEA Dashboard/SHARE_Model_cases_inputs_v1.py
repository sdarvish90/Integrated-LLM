from SHARE_model_v1  import *

cases_sheet = pd.read_excel('SHARE_model_input_sheet_v1_Alfanar.xlsx', sheet_name=2)
cases_sheet = cases_sheet.dropna()
# print(len(cases_sheet))
# exit()

cases_sheet ['PV_scaling_factor'] = cases_sheet['PV_capacity_MW'] / PV_capacity_ref_AC
if wind_interpolation == "linear scaling":
    cases_sheet['wind_scaling_factor'] = cases_sheet['Wind_MW'] / wind_capacity_ref
else:
    cases_sheet['wind_scaling_factor'] = 1



cases_sheet.columns = [name.split(' ')[0] for name in cases_sheet.columns]
result_rows = []
cases_rows = []
for index, case in cases_sheet.iterrows():
    print('running case ' + str(index + 1))

    PV_capacity = case.PV_capacity_MW
    PV_capacity_augmenatation_year = case.PV_capacity_augmentation_year
    PV_capacity_augmenatation_per = case.PV_capacity_augmenatation_per
    wind_capacity = case.Wind_MW
    wake_losses = case.Wake_loss_factor
    BESS_power = case.BESS_Power_MW
    BESS_energy = case.BESS_energy_MWh
    H2_storage_capacity_tonnes = case.H2_Storage_capacity_tonne
    PV_scaling_factor = case.PV_scaling_factor
    wind_scaling_factor = case.wind_scaling_factor
    electrolyser_capacity = case.Electrolyzer_capacity_MW
    NH3_plant_capacity_ratio = case.Ratio_NH3_Plant_capacity
    HB_capacity = case.HB_capacity_MW
    plant_capacity = case.Total_plant_capacity_MW
    grid_import_capacity = case.Grid_import_capacity_MW
    grid_export_capacity = case.Grid_export_capacity_MW
    grid_capex = case.Grid_CAPEX_kUSD * 1000
    grid_opex = case.Grid_OPEX_kUSD_per_year * 1000
    wind_capex = case.Wind_CAPEX_kUSD * 1000
    wind_opex = case.Wind_OPEX_kUSD_per_year * 1000
    PV_capex = case.PV_CAPEX_kUSD * 1000
    PV_opex = case.PV_OPEX_kUSD_per_year * 1000
    BESS_capex = case.BESS_CAPEX_kUSD * 1000
    BESS_opex = case.BESS_OPEX_kUSD_per_year * 1000
    electrolyser_capex = case.Electrolyser_CAPEX_kUSD * 1000
    electrolyser_opex = case.Electrolyser_OPEX_kUSD_per_year * 1000
    H2_BOP_capex = case.H2_BOP_CAPEX_kUSD * 1000
    H2_BOP_opex = case.H2_BOP_OPEX_kUSD_per_year * 1000
    H2_storage_capex = case.H2_Storage_CAPEX_kUSD * 1000
    H2_storage_opex = case.H2_Storage_OPEX_kUSD_per_year * 1000
    HB_capex = case.HB_CAPEX_kUSD * 1000
    HB_opex = case.HB_OPEX_kUSD_per_year * 1000
    NH3_storage_capex = case.NH3_Storage_CAPEX_kUSD * 1000
    NH3_storage_opex = case.NH3_Storage_OPEX_kUSD * 1000 
    NH3_Storage_fixed_opex = case.NH3_Storage_fixed_OPEX_kUSD * 1000
    process_plant_fixed_opex = case.Process_plant_fixed_OPEX_kUSD * 1000
    NH3_storage_capacity = case.NH3_storage_capacity_m3
    PV_ppa   = 42.0  # USD/MWh #Houston
    wind_ppa = 30.0  # USD/MWh #Houston





    result = simulator(PV_capacity, PV_capacity_augmenatation_year, PV_capacity_augmenatation_per,wind_capacity, BESS_power, BESS_energy, PV_scaling_factor, wind_scaling_factor, wake_losses,electrolyser_capacity, HB_capacity, H2_storage_capacity_tonnes, NH3_plant_capacity_ratio,  plant_capacity, grid_import_capacity, 
    grid_export_capacity, grid_capex, grid_opex, wind_capex, wind_opex, PV_capex, PV_opex, BESS_capex, BESS_opex, electrolyser_capex, electrolyser_opex, H2_BOP_capex, H2_BOP_opex, H2_storage_capex,
    H2_storage_opex, HB_capex, HB_opex, NH3_storage_capex, NH3_storage_opex, NH3_Storage_fixed_opex, process_plant_fixed_opex, NH3_storage_capacity)
    result_rows.extend(result)

print(cases_sheet)