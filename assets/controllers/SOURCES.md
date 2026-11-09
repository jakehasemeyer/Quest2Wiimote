Quest 2 Touch controller assets downloaded directly from Valve's SteamVR content depot on 2026-10-25.

- Steam app: 250820; content depot: 250824; manifest: 3693738476548293851.
- Original files are retained without geometry, texture, scale or origin edits. SHA-256 hashes are in source.json.
- These SteamVR assets belong to their respective owners and are separate from this project's source-code license.
- Downloaded using SteamRE/DepotDownloader 3.4.0 with anonymous Steam access and a file filter limited to the two oculus_quest2_controller directories. The full SteamVR application is not required at runtime.
- Alignment uses each model's explicit components.openxr_aim.component_local origin and rotate_xyz. The inverse of this transform puts the mesh in the sender's OpenXR aim space. Units are meters; no fit-to-box scaling, centering, hand mirroring or guessed offset is applied.

References:
https://github.com/ValveSoftware/openvr/wiki/Render-Model-Reference
https://github.com/ValveSoftware/openvr/wiki/Driver-Render-Models
https://github.com/SteamRE/DepotDownloader
