# CSP Automation v1 — status

## Zaimplementowane

- [x] format Shorta YAML i walidacja fikcji
- [x] dokładnie 8 scen
- [x] zgodność zatwierdzonej narracji z segmentami TTS
- [x] Z-Image Turbo workflow API oparty na core nodes ComfyUI
- [x] low-VRAM INT8/FP4 configuration dla RTX 4060 Ti 8 GB
- [x] sekwencyjne generowanie i resume istniejących scen
- [x] zwalnianie modeli ComfyUI po generowaniu obrazów
- [x] Chatterbox Multilingual V3 / język polski
- [x] jedna referencja głosu dla wszystkich scen
- [x] dokładne timingi scen z TTS
- [x] rzeczywista cisza przed sceną 8 / twistem
- [x] faster-whisper word timestamps
- [x] napisy SRT i ASS w blokach 2–5 słów
- [x] proceduralny roomtone / drone / twist impact
- [x] ruch scen zależny od długości wypowiedzi
- [x] FFmpeg NVENC 1080x1920
- [x] trwały output poza checkout GitHub
- [x] instalator modeli Z-Image z resume
- [x] preflight sprzętu, CUDA, FFmpeg i ComfyUI
- [x] instalator GitHub self-hosted runnera
- [x] smoke test `001-drzwi-0`

## Do potwierdzenia na komputerze produkcyjnym

- [ ] `PREFLIGHT OK` na RTX 4060 Ti 8 GB
- [ ] pierwszy rzeczywisty obraz Z-Image przy 768x1344
- [ ] brak OOM podczas przejścia Z-Image -> Chatterbox
- [ ] naturalność polskiego Chatterbox V3
- [ ] dokładność Whisper przy wybranym głosie
- [ ] działanie filtra ASS w lokalnym buildzie FFmpeg
- [ ] jakość push/pan na finalnym MP4
- [ ] poziomy proceduralnego sound designu
- [ ] pełny `final.mp4` z workflow `Build CSP Short`

PR pozostaje draftem do pierwszego udanego renderu na docelowym komputerze.
