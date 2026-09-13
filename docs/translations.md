# Translations

English is Groovia's source language and its fallback when no matching catalog
is available. Translations and the source template live in `po/`.

After adding or changing user-visible text, update the template and catalogs:

```sh
meson setup build --reconfigure
meson compile -C build groovia-pot
meson compile -C build groovia-update-po
```

To add a language, initialize its catalog from `po/groovia.pot`, add its locale
code to `po/LINGUAS`, then translate the new `po/<locale>.po` file:

```sh
msginit --input=po/groovia.pot --locale=de --output-file=po/de.po
```

Validate a catalog with:

```sh
msgfmt --check --check-format po/fr.po
```

Test the development build in a specific language with:

```sh
LANGUAGE=fr ./build/src/groovia
LANGUAGE=en ./build/src/groovia
```

Every new user-visible Python string must use `_()`, `ngettext()`, or
`pgettext()` from `src.i18n`. Mark visible GTK Builder properties with
`translatable="yes"` and add every source containing messages to
`po/POTFILES.in`.
