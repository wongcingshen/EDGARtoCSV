import json
import pandas as pd 
import csv
import os
from os import listdir
from os.path import isfile, join

dir_path = "C:/APIs/EDGAR"
output_path = "C:/APIs/EDGAR/Output"

# List to hold all the DataFrames generated in the loop
dfs = []

# get all json full paths of the json files in dir_path
all_json_files = [join(dir_path, f) for f in listdir(dir_path) if isfile(join(dir_path, f)) and f.endswith(".json")]

# iterate over the paths 
for file_path in all_json_files:
    with open(file_path) as input_file:
        data = json.load(input_file)
# Determine the output filename based on the input filename
        output_file_path = os.path.join(output_path, os.path.splitext(os.path.basename(file_path))[0] + '.csv')

# Access the nested dictionaries using keys, determine if it's gaap or ifrs
    if 'us-gaap' in data['facts']:
            us_gaap = data['facts']['us-gaap']
    else:
            us_gaap = data['facts']['ifrs-full']

# Extract the latest 'end' and 'val' values for the 'StockholdersEquity' item
    assets = us_gaap['Assets']
    usd_list = assets['units']['USD']
    assets_end = usd_list[-1]['end']

# Loop through the 'us-gaap' dictionary and extract the latest 'end' and 'val' values for each financial item
    results = []
    for key, value in us_gaap.items():
       
        if isinstance(value, dict) and 'units' in value and 'USD' in value['units']:
            usd_list = value['units']['USD']
            if usd_list:
                latest_data = usd_list[-1]
                if latest_data['end'] >= assets_end:
                    results.append({
                        'item': key,
                        # 'end': latest_data['end'],
                        'value': latest_data['val']
                    })

    df = pd.DataFrame(results)

    if not os.path.exists(output_path):
        os.makedirs(output_path)
    df.to_csv(output_file_path, index=False, sep='|')