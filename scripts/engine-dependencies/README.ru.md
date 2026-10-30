# Закреплённые зависимости движков

Dockerfile получает исходники AWG/WG по прежним точным commit, затем заменяет
только `go.mod` и `go.sum` файлами из соответствующего каталога. Исходники
протоколов не патчатся. Сборка использует Go 1.27.3 и проверку `go mod verify`.

Security-обновление: `golang.org/x/net v0.60.0` и требуемый им
`golang.org/x/crypto v0.57.0`. Транзитивные версии фиксируются в `go.mod`,
контрольные суммы — в `go.sum`. Автоматическое скачивание другой версии Go
при `go mod download` отключено через `GOTOOLCHAIN=local`.

Для обновления скопируйте исходные `go.mod`/`go.sum` выбранного движка в
временный каталог и выполните закреплённым Go:

```bash
go get golang.org/x/crypto@v0.57.0 golang.org/x/net@v0.60.0
go mod download
go mod verify
```

Перенесите полученные lock-файлы сюда, проверьте diff и пересоберите оба
production target. Обязательны повторный vulnerability scan, upstream
regression, совместимость прежних профилей и полные dual-network проверки.
