import json
import sys
from dotenv import dotenv_values

# Get the environment (dev or prod) from command line arguments
if len(sys.argv) != 2 or sys.argv[1] not in ['dev', 'prod']:
    print("Usage: python update_zappa_envs.py [dev|prod]")
    sys.exit(1)

environment = sys.argv[1]

# Load the appropriate .env file based on the environment
env_file = f".env.{environment}"
env_vars = dotenv_values(env_file)

# Load zappa_settings.json
with open("zappa_settings.json", "r") as f:
    zappa_settings = json.load(f)

# Update the environment_variables section for the specific environment in zappa_settings
zappa_settings[environment]["aws_environment_variables"] = env_vars

# Save back to zappa_settings.json
with open("zappa_settings.json", "w") as f:
    json.dump(zappa_settings, f, indent=4)

print(f"Environment variables from {env_file} have been added to zappa_settings.json")
