import pandas as pd
import json
import os
import sys
from datetime import time

def custom_serializer(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, time):
        return obj.strftime("%H:%M:%S")
    raise TypeError(f"Type {type(obj)} not serializable")

def convert_excel_to_json(input_file_path):
    # Check if file exists
    if not os.path.exists(input_file_path):
        print(f"Error: File '{input_file_path}' not found.")
        return

    try:
        # Read the Excel file
        print(f"Reading file: {input_file_path}...")
        xls = pd.ExcelFile(input_file_path)
        
        output_data = {}
        
        # Define expected sheets and their processing logic
        # 'metadata' is special: it converts from Key-Value rows to a single dictionary
        if 'metadata' in xls.sheet_names:
            df_meta = pd.read_excel(xls, sheet_name='metadata')
            # Convert 2-column dataframe to a single dictionary
            # Assuming columns are roughly 'key' and 'value'
            if df_meta.shape[1] >= 2:
                output_data['metadata'] = pd.Series(
                    df_meta.iloc[:, 1].values, index=df_meta.iloc[:, 0]
                ).to_dict()
            else:
                output_data['metadata'] = df_meta.to_dict(orient='records')
        
        # Process other standard sheets
        standard_sheets = ['employees', 'vehicles', 'baseline']
        
        for sheet in standard_sheets:
            if sheet in xls.sheet_names:
                df = pd.read_excel(xls, sheet_name=sheet)
                # Convert DataFrame to list of dictionaries
                output_data[sheet] = df.to_dict(orient='records')
            else:
                print(f"Warning: Sheet '{sheet}' not found in workbook.")

        # Generate output filename
        base_name = os.path.splitext(os.path.basename(input_file_path))[0]
        output_file_name = f"{base_name}_parsed.json"
        
        # Write to JSON file
        with open(output_file_name, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, default=custom_serializer)
            
        print(f"Successfully created: {output_file_name}")

    except Exception as e:
        print(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python data_converter.py <path_to_excel_file>")
    else:
        convert_excel_to_json(sys.argv[1])