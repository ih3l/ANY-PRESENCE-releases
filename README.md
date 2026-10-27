# ANY-PRESENCE

Show any game or program as your Discord Rich Presence, even ones that have no Discord support.

Pick a game, and ANY-PRESENCE sets its title, cover and status on your profile. You can change everything by hand.

<img width="860" height="580" alt="image" src="https://github.com/user-attachments/assets/7cc7229a-b9de-43ab-8fd2-eceba7c993bc" />

## Features

- **Search** Steam, GOG, VNDB, DLsite, Wikipedia, Lutris and the Internet Archive to find the right title and cover
- **Your own icons:** drop, paste or choose any image, and crop it
- **Program icons** are added automatically (needs a free ImgBB key)
- **Saved apps** turn the presence on by themselves when the program opens, and go back to the previous one when it closes
- **Buttons** on your presence.
- **Recent games:** play or launch a game in one click, with banners from Steam, GOG and DLsite, or your own
- **Automatic updates** from this page, with the changes shown before you update

## Setup

1. Download `ANY-PRESENCE.exe` from the [latest release](../../releases/latest).
2. Create an application at [discord.com/developers/applications](https://discord.com/developers/applications).
3. Copy its **Application ID** into ANY-PRESENCE → Settings.
4. Add a free [ImgBB](https://api.imgbb.com) API key in Settings so your own images and program icons show on Discord. *(Optional)*

Windows only. Discord must be open on the same computer.

## Privacy

Everything runs on your computer. The app only contacts the game stores you search, ImgBB (if you add a key), Discord, and GitHub (to check for updates). Your settings are saved in `%APPDATA%\ANY-PRESENCE`.
