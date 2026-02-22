from .lns_algo import LNSOptimizer

def solve_vrp(input_data, matrix_edge_list, file_bytes):
    """
    Main Solver Function using LNS.
    Args:
        input_data (dict): The original payload (employees, vehicles, metadata).
        matrix_edge_list (list): The flat list of edge costs from logic.py.
        file_bytes (bytes): The raw Excel file bytes.
    """
    # Initialize LNS Optimizer
    # We can pass initial_sol as None for now, or try to build a simple one.
    lns = LNSOptimizer(file_bytes, matrix_edge_list)
    
    # Run optimization
    # Adjust max_iterations based on performance needs. 
    # For a request-response cycle, maybe 50-100 is enough.
    lns.optimize(max_iterations=50)
    
    # Get formatted result
    result = lns.get_formatted_output()
    
    return result