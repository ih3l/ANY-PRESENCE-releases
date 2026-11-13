# ANY-PRESENCE

Show any game or program as your Discord Rich Presence, especially those that have no Discord support.

Pick a game, and ANY-PRESENCE sets its title, cover and status on your profile. You can change everything by hand.

<img width="844" height="541" alt="image" src="https://github.com/user-attachments/assets/56408192-17a0-43cd-8767-1b29d8b93bee" />

## Features

- **Music:** show what you're playing on SoundCloud and Spotify, with synced lyrics from [LRCLIB](https://lrclib.net). Works with the Spotify desktop app, or with the ANY-PRESENCE Web browser extension. Pick the lyrics, status, cover and button in the Music tab.
- **Search** Steam, GOG, VNDB, DLsite, Wikipedia, Lutris and the Internet Archive to find the right title and cover
- **Your own icons:** drop, paste or choose any image, and crop it
- **Program icons** are added automatically (needs a free ImgBB key)
- **Saved apps** turn the presence on by themselves when the program opens.
- **Buttons** on your presence.
- **My Games:** play or launch a game in one click and track your hours played.
- **Game info:** click a game's title in My Games or in the search results to see its store page info (description, screenshots, trailers, ratings and details) from Steam, GOG, DLsite or VNDB.
- **Themes:** five to choose from, plus one you design yourself.


## Website and Terms
 
- Check the [ANY-PRESENCE website](https://ih3l.github.io/ANY-PRESENCE-releases/) for more info and a live demo of the app.
- **Read the [Terms & Privacy](https://ih3l.github.io/ANY-PRESENCE-releases/terms.html)** before using the app. It explains what you agree to, what stays on your computer and what the app sends, and when.

## Setup

1. Download `ANY-PRESENCE.exe` from the [latest release](../../releases/latest).
2. Open it, with Discord open on the same computer, and search a game or pick a running program, then press **Start**.
3. Add a free [ImgBB](https://api.imgbb.com) API key in Settings so your own images and program icons show on Discord. *(Optional)*

ANY-PRESENCE comes with its own Discord application, so there's nothing to create. If you prefer your own, make one at [discord.com/developers/applications](https://discord.com/developers/applications) and paste its **Application ID** at the bottom of Settings. *(Optional)*

Windows only.

## Windows says it's a virus?

ANY-PRESENCE isn't code-signed (it cost money), and it does things antivirus programs are wary of: it can start with Windows, and it reads your running programs to find the game you're playing. Because of that, Windows SmartScreen or your antivirus may warn about it or flag it by mistake.

So:
- **Read the code.** All of it is in this repo. If you're not a developer, ask someone you trust to review it.
- **Build it yourself.** Install Python and run `build.bat` to make your own copy.

If Windows blocks it, click **More info → Run anyway**.

## Privacy
 
Everything runs on your computer. The app only contacts the game stores you search, ImgBB (if you add a key), Discord, and GitHub (to check for updates). Your settings, My Games list and hours played are saved in `%APPDATA%\ANY-PRESENCE`, along with small copies of the banners.
