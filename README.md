# ANY-PRESENCE

Show any game or program as your Discord Rich Presence, even ones that have no Discord support.

Pick a game, and ANY-PRESENCE sets its title, cover and status on your profile. You can change everything by hand.

<img width="860" height="580" alt="image" src="https://github.com/user-attachments/assets/fda67c93-bffc-4507-9f52-5764213a2a01" />

## Features

- **Search** Steam, GOG, VNDB, DLsite, Wikipedia, Lutris and the Internet Archive to find the right title and cover
- **Your own icons:** drop, paste or choose any image, and crop it
- **Program icons** are added automatically (needs a free ImgBB key)
- **Saved apps** turn the presence on by themselves when the program opens

## Setup

1. Download `ANY-PRESENCE.exe` from the [latest release](../../releases/latest).
2. Create an application at [discord.com/developers/applications](https://discord.com/developers/applications).
3. Copy its **Application ID** into ANY-PRESENCE → Settings.
4. *(Optional)* Add a free [ImgBB](https://api.imgbb.com) API key in Settings so your own images and program icons show on Discord.

Windows only. Discord must be open on the same computer.

## Privacy

Everything runs on your computer. The app only contacts the game stores you search, ImgBB (if you add a key), Discord, and GitHub (to check for updates). Your settings are saved in `%APPDATA%\\ANY-PRESENCE`.
