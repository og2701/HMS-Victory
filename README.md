# HMS Victory

[![codecov](https://codecov.io/gh/og2701/HMS-Victory/branch/main/graph/badge.svg?token=YOUR_CODECOV_TOKEN)](https://codecov.io/gh/og2701/HMS-Victory)

## Getting Started

To get started with HMS Victory, follow these steps:

1. **Clone the repository**

```bash
git clone https://github.com/og2701/hms-victory.git
```

2. **Change into the directory**

```bash
cd hms-victory
```

3. **Install the required dependencies**

```bash
pip install -r requirements.txt
```

4. **Add your Discord bot token**

- Open the `config.py` file located in the root directory.
- Replace the placeholder values with your actual Discord bot token and define role button configurations. The number of roles configured in `ROLE_BUTTONS` determines the number of role assignment buttons displayed by the role-react command. This feature allows users to assign roles to themselves by clicking buttons related to each role described:

```python
# config.py
TOKEN = "your_discord_bot_token_here"

ROLE_BUTTONS = {
    "1234567891234567891": {                    # role id
        "name": "some role name",               # role name
        "description": "some role description"  # role description
    },
    "1234567891234567891": {
        "name": "some role name", 
        "description": "some role description"
    },
    ...
}

```

### Prerequisites

- Python 3.12.2 or higher (earlier versions would probably work but are not tested)
- pip (Python package installer)

## Usage

After setting up your Discord bot token in `config.py`, run the bot script from the root of the project:

```bash
python run.py
```

## Development

This project uses GitHub Actions for continuous integration. Upon pushing code or creating a pull request to the main branch, the CI workflow will automatically run tests and linting. To add new features or commands, follow the project's coding standards and update the tests accordingly.

## Contributing

Contributions to HMS Victory are welcome.

## License

N/A

## Acknowledgments

- N/A
