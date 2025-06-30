import discord
import aiohttp
import os
import asyncio
import imgkit
from datetime import datetime

WEATHER_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY") 

UKP_ACCENT = "#F44336"

CITIES = {
    "London": (51.5072, -0.1276),
    "Manchester": (53.4808, -2.2426),
    "Birmingham": (52.4862, -1.8904),
    "Glasgow": (55.8642, -4.2518),
    "Belfast": (54.5973, -5.9301),
    "Cardiff": (51.4816, -3.1791),
    "Edinburgh": (55.9533, -3.1883),
    "Liverpool": (53.4084, -2.9916),
}

async def get_weather_data(session, city, lat, lon):
    url = f"http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric"
    try:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                return {
                    "city": city,
                    "temp": data["main"]["temp"],
                    "description": data["weather"][0]["description"],
                    "icon_url": f"http://openweathermap.org/img/wn/{data['weather'][0]['icon']}@2x.png",
                }
            else:
                return None
    except Exception:
        return None

async def generate_weather_html_image(weather_data: list):
    with open("templates/weather_report.html", "r", encoding="utf-8") as f:
        html_template = f.read()

    cards_html = ""
    for data in weather_data:
        if data:
            cards_html += f"""
            <div class="weather-card">
                <h2>{data['city']}</h2>
                <img class="weather-icon" src="{data['icon_url']}" alt="{data['description']}">
                <div class="temperature">{data['temp']:.0f}°C</div>
                <div class="description">{data['description']}</div>
            </div>
            """
    
    final_html = html_template.replace("{{ weather_cards }}", cards_html)

    options = {'width': 590, 'encoding': "UTF-8", 'custom-header': [('Accept-Encoding', 'gzip')], 'disable-smart-width': ''}
    
    try:
        image_bytes = imgkit.from_string(final_html, False, options=options)
        return image_bytes
    except Exception as e:
        print(f"Error generating image with imgkit: {e}")
        return None


async def weather_command(interaction: discord.Interaction):
    if not WEATHER_API_KEY:
        await interaction.response.send_message("Weather API key is not configured.", ephemeral=True)
        return
        
    await interaction.response.defer()

    async with aiohttp.ClientSession() as session:
        tasks = [get_weather_data(session, city, lat, lon) for city, (lat, lon) in CITIES.items()]
        weather_results = await asyncio.gather(*tasks)

    weather_data = [res for res in weather_results if res]
    if not weather_data:
        await interaction.followup.send("Could not retrieve weather data. Please try again later.")
        return

    image_bytes = await generate_weather_html_image(weather_data)
    if not image_bytes:
        await interaction.followup.send("There was an error generating the weather report image.")
        return
        
    file = discord.File(fp=io.BytesIO(image_bytes), filename="uk_weather.png")

    embed = discord.Embed(
        title="UKPlace Weather Report",
        description="Here is the current weather across the United Kingdom.",
        color=discord.Color.from_str(UKP_ACCENT)
    )
    embed.set_image(url="attachment://uk_weather.png")
    embed.set_footer(text=f"Generated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")

    await interaction.followup.send(embed=embed, file=file)