import os
from timeit import default_timer as timer

import pandas as pd

from helpers.postprocessing import beautiful_rename, heat_map2d, heat_map3d, heat_map3d_gif

start = timer()
directory = os.path.dirname(os.path.abspath(__file__))
directory = os.path.join(directory, "Summary_output")
#cases_df = merge_cases(directory)
os.chdir(directory)
cases_df = pd.read_excel("cases.xlsx")
cases_df = beautiful_rename(cases_df)

## LCOA plot ##
heat_map2d(df = cases_df,
           index = "Haber-Bosch to Electrolysis Ratio [%]", 
           column = "Electrolyser Capacity [MW]", 
           value = "Total LCOA [$/tonnes]", 
           type = "low",
           format = '.0f')

## IRR plot ##
heat_map2d(df = cases_df,
           index = "Haber-Bosch to Electrolysis Ratio [%]", 
           column = "Electrolyser Capacity [MW]", 
           value = "IRR [%]", 
           type = "high",
           format = '.2f')

heat_map3d(df = cases_df,
           x = "Haber-Bosch to Electrolysis Ratio [%]", 
           y = "Electrolyser Capacity [MW]", 
           z = "Total LCOA [$/tonnes]"
           )

heat_map3d_gif(df = cases_df,
           x = "Haber-Bosch to Electrolysis Ratio [%]", 
           y = "Electrolyser Capacity [MW]", 
           z = "Total LCOA [$/tonnes]"
           )
print('The program took', timer() - start,'to run')