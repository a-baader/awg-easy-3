# Подготовка и публикация стабильного выпуска

`master` всегда соответствует уже опубликованному stable: обычный
`git clone https://github.com/a-baader/wg-dual-easy.git` получает установщик,
для версии которого доступны manifest и образы. Подготовка новой VERSION,
package.json, Compose и release notes выполняется в `release/ВЕРСИЯ`,
а не в default branch. Изменение `master` требует четырёх статусов:
`node`, `container`, `dual`, `stable-release`. Force push и удаление запрещены.

1. Создайте release-ветку от проверенного изменения, обновите metadata и notes.
   Выполните `python3 scripts/sync-doc-versions.py`; `--check` проверяет версии
   текущих страниц без изменения файлов и выполняется в CI.
2. Отправьте ветку и дождитесь полного успешного `test.yml` для её точного commit.
   В CI сохраняются архив тех же образов, которые прошли сетевые, lifecycle,
   recovery и browser-проверки, их image ID и SHA-256 архива.
3. Создайте неизменяемый тег `vВЕРСИЯ` на этом же commit. Перезапись и удаление
   `v*`-тегов защищены правилами репозитория.
4. Запустите `docker-publish.yml` на теге с `publish=true`. Publisher получает
   артефакт успешного CI этого commit и публикует те же image ID без пересборки.
   Если артефакт уже истёк (14 дней), повторите полный CI.
5. Образы получают проверяемый provenance. Publisher создаёт draft, загружает
   assets без `--clobber`, проверяет анонимный доступ к обоим digest и публикует
   готовый stable. Неизменяемость применяется к новым опубликованным релизам.
6. Только затем обновляется `latest`, выставляется `stable-release` и `master`
   продвигается fast-forward на тот же commit. RC в `master` не продвигаются.

Повторная публикация существующего релиза или version tag образа запрещена.
Не переписывайте опубликованные assets и теги для исправления ошибки:
подготовьте новый номер версии. Если default branch изменился параллельно,
автоматического merge/force push нет; требуется новое согласованное продвижение.

Проверка provenance выполняется по точному digest:

```sh
gh attestation verify oci://ghcr.io/a-baader/wg-dual-easy-wg@sha256:DIGEST \
  --repo a-baader/wg-dual-easy \
  --signer-workflow a-baader/wg-dual-easy/.github/workflows/docker-publish.yml
```

Источники: [неизменяемые релизы](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes),
[проверка attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations).
