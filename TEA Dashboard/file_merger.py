import os
import pandas as pd
import glob
from timeit import default_timer as timer

start = timer()



# directory = 'C:\\Users\OUSELH\\OneDrive - DNV\SHARE Model\\0. Scripts\\0. Projects\\Synergy\\Summary_output'
directory = os.path.dirname(os.path.abspath(__file__))
os.chdir(directory)

data = []
extension = 'csv'
all_filenames = [i for i in glob.glob('*.{}'.format(extension))]
# print(all_filenames)
for csv in all_filenames:
    frame = pd.read_csv(csv)
    # frame['case'] = os.path.basename(csv)
    frame.insert(0, 'Case', os.path.basename(csv))
    data.append(frame)

combined_csv = pd.concat(data, ignore_index=True) 
combined_csv['Case'] = combined_csv['Case'].str.replace('.csv', '')
combined_csv.to_excel( "cases.xlsx", index=False)

print('The program took', timer() - start,'to run')

