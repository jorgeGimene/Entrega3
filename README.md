# Práctica 3 · PokéAPI segura

API HTTP/JSON para una Pokédex personal. La [memoria técnica](memoria.pdf) explica las comprobaciones de seguridad. Esta versión usa Flask y ejecuta las peticiones una a una en local.

## Ejecutar

Usa Python 3.12. En Linux o WSL, desde la carpeta que contiene `api.py`:

python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python api.py


Si instalaste Python con `uv` y ya tienes el entorno `.venv312`, puedes usarlo desde esta misma carpeta si está ahí:


"$HOME/.local/bin/uv" pip install --python .venv312/bin/python -r requirements.txt
.venv312/bin/python api.py


Abre http://127.0.0.1:8080/version para comprobar el arranque. El servidor está pensado para HTTP local con un solo proceso. Ctrl+C lo detiene. Las cuentas y las entradas de la Pokédex se conservan en `data/`. Al reiniciar, hay que volver a iniciar sesión y los contadores de intentos empiezan de cero. Puedes elegir otra carpeta con la variable POKEAPI_DATA.

## Primer recorrido

Envía JSON con Content-Type application/json. Los pasos se pueden hacer con un cliente HTTP como curl o Postman.

| Método | Ruta | Cuerpo o resultado |
|---|---|---|
| GET | /version | Nombre y versión |
| POST | /signup | Envía {"username":"ash","password":"demo-local"} y obtendrás 201 |
| POST | /login | Envía las mismas credenciales y copia el valor de token |
| PUT | /user/ash/pokedex/25 | Envía el documento de abajo. La primera escritura devuelve 201 y las siguientes 200 |
| GET | /user/ash/pokedex | Resumen de las entradas propias |
| GET | /user/ash/pokedex/25 | Documento completo |
| DELETE | /user/ash/pokedex/25 | 204 con el cuerpo vacío |

En las cuatro operaciones de Pokédex añade la cabecera `Authorization: Bearer <token>`, sustituyendo el marcador por el valor obtenido en el login.


{
  "dex": 25,
  "name": "Pikachu",
  "types": ["electric"],
  "attacks": [{"name": "Thunder Shock", "type": "electric", "level": 1}]
}


## Ejecutar las pruebas

Desde la carpeta del proyecto, usa el Python del entorno donde instalaste Flask:


.venv/bin/python -m unittest -v


Si tu entorno se llama `.venv312`, sustituye `.venv` por `.venv312`. Las pruebas usan una carpeta temporal y el cliente de Flask. Comprueban el registro, el inicio de sesión, el acceso por propietario, la caducidad, la validación, los límites y la persistencia de la Pokédex.
