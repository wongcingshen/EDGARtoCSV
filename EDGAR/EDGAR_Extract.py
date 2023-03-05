import json
import pandas as pd 
import csv
import os
from os import listdir
from os.path import isfile, join

dir_path = "testJSON"
output_path = "Output"
ExclusionList = "Exclusion.txt"

# List to hold all the DataFrames generated in the loop
dfs = []
df_all = pd.DataFrame()

# get all json files in dir_path
all_json_files = [join(dir_path, f) for f in listdir(dir_path) 
                  if isfile(join(dir_path, f)) and f.endswith(".json")]


# iterate over the json files 
for file_path in all_json_files:
    with open(file_path) as input_file:
        data = json.load(input_file)
        
        # Check if 'entityName' exists in the JSON data
    if 'entityName' not in data:
        if 'cik' not in data:
            print("No entityName or cik found in file: " + file_path)
            continue
        else:
            cik_noname = data['cik']
            print("No entityName found for CIK: " + cik_noname)
            continue  # skip this file and move on to the next one

        # Extract companies' names
    companyName = data['entityName']

    # Access the nested dictionaries using keys, determine if it's gaap or ifrs
    try:
        if 'us-gaap' in data['facts']:
            us_gaap = data['facts']['us-gaap']
        else:
            us_gaap = data['facts']['ifrs-full']
    except KeyError:
        cik_nogaap = data['cik']
        print(file_path + " has neither us-gaap nor ifrs-full found in data['facts']")
        continue 

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
                if latest_data['end'] == assets_end:
                    results.append({
                        'item': key,
                        'Financial End': latest_data['end'],
                        'value': latest_data['val'],
                        'Company': companyName,
                    })

 # Put results into a DataFrame and append to the list of DataFrames 
    df = pd.DataFrame(results)
    dfs.append(df)

 # Concatenate all the DataFrames in the list to create a single DataFrame
    df_all = pd.concat(dfs, ignore_index=True)

 # Remove the items in the ExclusionList from the DataFrame
    with open(ExclusionList) as f:
        ExclusionStr = f.read().splitlines()

    df_all = df_all[~df_all['item'].isin(ExclusionStr)]
    
    df_all['item'] = df_all['item'].replace(['CostOfRevenue', 'CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization','CostOfGoodsSold'], 'CostOfRevenue')

    # Add 'end' to the list of items in the pivot table
    df_pivoted = df_all.pivot_table(index=['Company', 'Financial End'], columns='item', values='value', aggfunc='first').reset_index()

# Merge revenue items and sum their values
# df_pivoted['item'] = df_pivoted['item'].replace(['SalesRevenueNet', 'SalesRevenueServicesNet', 'SalesRevenueGoodsNet'], 'Revenue')
# df_pivoted = df_pivoted.groupby(['Company', 'Financial End', 'item'], as_index=False)['value'].agg('sum')


if not os.path.exists(output_path):
     os.makedirs(output_path)
df_pivoted.to_csv(output_path + "/Sample.csv", index=False)

