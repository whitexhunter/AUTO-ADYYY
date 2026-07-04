FROM python:3.12-slim

WORKDIR /app

# Copy the nested folder contents
COPY / .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Run the bot
CMD ["python", "main.py"]
