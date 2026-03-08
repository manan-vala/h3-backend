# Base image updated to 3.10 to satisfy both Vroom and click==8.3.1
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install system dependencies (curl for healthchecks)
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Copy requirements and install main app dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Vroom-specific dependencies
RUN pip install --no-cache-dir "numpy<2" pandas openpyxl

# Copy the rest of the application code
COPY . .

# Expose the port
EXPOSE 8080

# Command to run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]