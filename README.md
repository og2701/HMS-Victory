# HMS Victory 

## Getting Started

### Prerequisites

Before you begin, make sure you have the following:

*   **Python 3.8+**: The bot is built using Python, requiring a compatible version.
*   **Discord Bot Token**: Get this from the Discord Developer Portal.
*   **OpenAI API Key**:  Needed for features like translation and the roast command.
*   **Chromium**: Required by certain commands.
*   **Dependencies**: Install required Python packages:

    ```bash
    pip install -r requirements.txt
    ```
    The `requirements.txt` file is created/updated using:
    ```bash
    pip freeze > requirements.txt
    ```

### Environment Setup

1.  **Environment Variables**:  Create a `.env` file in the project's root directory.  Add these lines, replacing placeholders with your actual keys:

    ```
    DISCORD_TOKEN=your_discord_bot_token_here
    OPENAI_TOKEN=your_openai_api_key_here
    ```

2.  **Configuration Files**: The bot uses several JSON files for data storage (e.g., `thread_messages.json`, `added_users.json`, `chat_leaderboard.json`, `persistent_views.json`, and files in the `daily_summaries` directory).  Make sure these files exist. Some are created automatically, but you might need to manually create empty files for others.

3. **Folder Structure**: Ensure you have created all the folders in the structure that is shown at the top of the Python file.

### Running the Bot

1.  **Navigate**:  Open a terminal/command prompt and go to the `HMS-Victory` directory.
2.  **Execute**: Run the bot:

    ```bash
    python run.py
    ```
    or
    ```bash
    bash start.sh
    ```

The bot should connect to Discord and start if set up correctly.

## Commands

The HMS Victory bot has various commands, categorized by required user permissions.

### Owner Only

*   `/role-manage` `role_name: str`: Assigns a specified role to all members without it. *Usable only by the bot owner (specified by `USERS.OGGERS` in `lib/constants.py`).*

### Minister, Cabinet

*   `/setup-announcement` `channel: TextChannel`: Sets up an announcement message, optionally with role-assigning buttons.
*   `/post-daily-summary` `date: str (optional)`: Posts the daily server summary.  If `date` is omitted, it defaults to today (UK time). Date format: `YYYY-MM-DD`.
*   `/post-last-weekly-summary`: Posts the summary for the last completed week (Monday to Sunday).
*   `/post-last-monthly-summary`: Posts the summary for the previous calendar month.
*   `/archive-channel` `seconds: int = 86400`: Archives the current channel, restricting posting.  It automatically moves the channel to the archive category after a specified delay (default: 24 hours).
*   `/add-to-iceberg` `text: str`, `level: int`: Adds text to the server's "iceberg" image at the given level.
*   `/vc-control` `user: Member`: Toggles voice chat mute/deafen for the specified user.

### Minister, Cabinet, Border Force

*   `/add-whitelist` `user: Member`: Adds a user to the whitelist for the politics channel.
*   `/politics-ban` `user: Member`: Toggles the "Politics Ban" role, preventing access to the politics channel.
*   `/embed-perms` `user: Member`: Toggles embed permissions for a user.

### Cabinet

*   `/lockdown-vcs`: Activates voice channel lockdown, muting/deafening all non-whitelisted members in voice channels.

### Minister, Cabinet, Border Force, PCSO, Server Booster

*   `/roast` `channel: TextChannel (optional)`, `user: Member (optional)`:  Generates a roast of a user, based on their recent messages in the channel. Defaults to the current channel and user if not specified. Limited daily uses.

### Everyone

*   `/rank` `member: Member (optional)`: Shows a user's rank card.
*   `/leaderboard`: Displays the server's XP leaderboard.
*   `/show-iceberg`: Shows the current server iceberg image.
*   `/screenshot-canvas` `x: int = -770`, `y: int = 7930`: Takes a screenshot of the pixelcanvas.io canvas at the given coordinates.
*   `/gridify` `attachment_url: str`: Adds a pixel art grid to an image.
*   `/colour-palette` `attachment_url: str`: Generates a colour palette from an image.

### Deprecated

These commands are still in the code, but are either replaced by other commands or used only in specific events.

*   `/role-react`: *Deprecated*, replaced by announcement command and persistent role buttons.
*   `/toggle-anti-raid`: *Deprecated*, only works if enabled by using the command.
*   `/toggle-quarantine`: *Deprecated*, only works if enabled by `/toggle-anti-raid`.
*   `/end-lockdown-vcs`: *Deprecated*, only works if enabled by `/lockdown-vcs`.

## Event Handlers

The bot includes event handlers that perform actions automatically:

*   **`on_ready`**: Synchronizes commands, reattaches persistent views, and schedules recurring jobs (summaries, cache clearing, backups).
*   **`on_message`**: Processes message attachments (caching images), handles message links, adds users to forum threads, and manages XP.
*   **`on_interaction`**: Handles interactions with components (e.g., role buttons).
*   **`on_member_join`**: Applies anti-raid checks (if enabled) and assigns the Member role.
*   **`on_message_delete`**: Logs deleted messages/attachments to the logs channel.
*   **`on_message_edit`**: Logs edited messages to the logs channel, showing before/after content.
*   **`on_reaction_add`**: Handles flag reactions for translation and the `:Shut:` reaction for temporary timeouts.
*   **`on_reaction_remove`**: Handles removal of the `:Shut:` reaction to cancel timeouts.
*   **`on_voice_state_update`**: Mutes/deafens members joining voice channels during a lockdown.

## Notes

*   The bot uses files to store data, including JSON files for configuration, user data, and summaries.
*   There's a scheduled job to create/post daily server summaries, zip daily summary files, and post weekly/monthly summaries. It also includes a backup job.
*   Image caching stores images sent in the server for faster logging of deleted images.
*   The bot uses `apscheduler` for scheduling.
