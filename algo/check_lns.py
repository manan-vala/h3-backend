import sys
import os

# Add root to path to find Utils
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)

try:
    from Utils.solution_check import validate_solution
except ImportError:
    # If running from Prog directly, maybe path is different?
    sys.path.append(os.path.join(root_dir, 'Utils'))
    from solution_check import validate_solution

if __name__ == "__main__":
    json_path = os.path.join(root_dir, "Output/output_lns.json")
    xlsx_path = os.path.join(root_dir, "Test_Data/TestCase_TC04.xlsx")
    
    if os.path.exists(json_path) and os.path.exists(xlsx_path):
        print(f"Checking {json_path}...")
        validate_solution(json_path, xlsx_path)
    else:
        print(f"Files not found:\n{json_path}\n{xlsx_path}")