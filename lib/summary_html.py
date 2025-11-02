from lib.image_processing import screenshot_html
from lib.file_operations import read_html_template


def calculate_estimated_height(content, line_height=20, base_height=100):
    message_lines = content.split("\n")
    total_lines = sum(len(line) // 80 + 1 for line in message_lines)
    content_height = line_height * total_lines
    estimated_height = max(base_height, content_height + 100)
    return estimated_height


async def create_summary_image(summary_data, title, title_color):
    total_members = summary_data["total_members"]
    members_joined = summary_data["members_joined"]
    members_left = summary_data["members_left"]
    members_banned = summary_data["members_banned"]
    total_messages = summary_data["total_messages"]
    reactions_added = summary_data["reactions_added"]
    reactions_removed = summary_data["reactions_removed"]
    deleted_messages = summary_data["deleted_messages"]
    boosters_gained = summary_data["boosters_gained"]
    boosters_lost = summary_data["boosters_lost"]
    top_channels = summary_data["top_channels"]
    active_members = summary_data["active_members"]
    reacting_members = summary_data["reacting_members"]

    top_channels_str = "\n".join(
        [
            f"<li>{channel_name}: {count} messages</li>"
            for channel_name, count in top_channels
        ]
    )
    active_members_str = "\n".join(
        [
            f"<li>{member_name}: {count} messages</li>"
            for member_name, count in active_members
        ]
    )
    reacting_members_str = "\n".join(
        [
            f"<li>{member_name}: {count} reactions</li>"
            for member_name, count in reacting_members
        ]
    )

    html_content = read_html_template("templates/summary.html").format(
        title=title,
        title_color=title_color,
        total_members=total_members,
        members_joined=members_joined,
        members_left=f"{members_left} ({members_banned} banned)",
        total_messages=total_messages,
        reactions_added=reactions_added,
        reactions_removed=reactions_removed,
        deleted_messages=deleted_messages,
        boosters=f"{boosters_gained} / {boosters_lost}",
        top_channels=top_channels_str,
        active_members=active_members_str,
        reacting_members=reacting_members_str,
    )

    estimated_height = calculate_estimated_height(html_content, base_height=400)

    return screenshot_html(html_content, size=(800, estimated_height))
