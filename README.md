# Laboratorio de IA - CENIT (Aula N° 1, UNLaR)

Modelo tridimensional y recorrido audiovisual para la propuesta de distribución y equipamiento del Laboratorio de Inteligencia Artificial (CENIT, Universidad Nacional de La Rioja).

## Contenido del Repositorio

- `build_cenit_lab.py`: Generación procedural de la geometría, iluminación y mobiliario base en Blender.
- `render_video.py`: Animación de cámaras, cartelería técnica informativa, trazado de infraestructura eléctrica, renderizado headless y ensamble de video.
- `relevamiento.pdf`: Especificaciones y relevamiento técnico del espacio físico.
- `photos/`: Fotografías del estado previo del aula.
- `renders/cenit_lab_walkthrough.mp4`: Video final renderizado (1920x1080, 24 fps, H.264).
- `renders/test/`: Capturas estáticas de referencia por plano.

## Requisitos

- Blender 4.2+ / 5.x
- FFmpeg

## Instrucciones de Ejecución

Generación de capturas de prueba por toma:
```powershell
blender -b -P render_video.py -- --test-stills
```

Generación de previsualización (1280x720):
```powershell
blender -b -P render_video.py -- --preview
```

Renderizado de video final (1920x1080):
```powershell
blender -b -P render_video.py -- --final
```
