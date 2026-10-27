kkLap music folder. Drop .ogg files here, rebuild the zip (python3 tools/build_client_zip.py),
upload the new zip. Every player downloads the zip when they join, so keep tracks small:
a 3 minute track at 96 kbps is about 2 MB. Convert with
    ffmpeg -i track.mp3 -c:a libvorbis -b:a 96k track.ogg
In game:  /lap music track        plays art/sound/kkLap/track.ogg for everyone
          /lap music track 0.3    at 30% volume
          /lap music stop
Only ship music you are allowed to distribute (the zip IS distribution).
