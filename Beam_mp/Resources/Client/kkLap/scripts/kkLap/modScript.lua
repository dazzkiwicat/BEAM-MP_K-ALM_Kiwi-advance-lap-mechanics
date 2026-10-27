-- kkLap client mod entry point. BeamMP runs this when the zip is loaded.
-- Lives at scripts/kkLap/modScript.lua, one folder down, matching BeamMP's own
-- scripts/BeamMP/modScript.lua. The guide's diagram shows it one level up; the real mod doesn't.
load('kkLap')
setExtensionUnloadMode('kkLap', 'manual')
log('I', 'kkLap', '[KKLAP] modScript ran, kkLap v0.20')   -- keep in step with M.VERSION
