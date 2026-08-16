"""
The canonical resource taxonomy, shared by every event.

Keys are English because they are database identifiers; names are Spanish because they are
what a Colombian coordinator reads on screen. The hierarchy is what lets a need for sleeping
mats be met by an offer of bedding when nothing closer exists, so a resource without a parent
can only ever match itself.

`humanitarian_aid` is the root of everything a collection center hands out — water, food,
medicine, hygiene, shelter and their children. That is what makes "tenemos un centro de acopio
de ayudas humanitarias" a usable offer instead of a dead node. Transport, machinery, power,
volunteers and cash stay outside it: a truck is not a donation, and an offer of aid must never
be proposed as a way to move it.

Note:
    Siblings never connect. Compatibility is a resource plus its ancestors and descendants, so
    an aid offer reaches a water need through the tree while a water offer still cannot cover a
    food need. Widening a category is therefore safe; flattening one is not.

    The extractor guesses one of these keys per item. An unrecognised guess is not an error —
    the ingest step creates it as a parentless leaf, which is exactly the "add a resource
    mid-emergency without a migration" case the table exists for. It just will not participate
    in category fallback until someone gives it a parent.

    Such a leaf is created with its name equal to its key, and that equality is the marker for
    "auto-created, never named by a human". Adding the key to `RESOURCES` and reloading adopts
    it: the seed fills in the Spanish name, the parent and the unit. A row somebody has already
    named keeps its name — but never its parent. The hierarchy is owned by this file, because a
    graph half of whose edges came from an old copy of it is worse than either version.

    `LEGACY_KEYS` exists because this seed replaced a data migration that keyed the catalog in
    Spanish, and `clear` only ever removed what this file declares. Any database seeded before
    the change still carries both halves of every entry, and a duplicate here is not cosmetic:
    `agua` beside `water` splits one resource into two that can never match each other.
"""

from collections.abc import Callable

from ayudagente.radar.models import Requirement, ResourceType

Writer = Callable[[str], None]  # progress sink: `stdout.write`, `print` or a test collector

# (key, display name, parent key, default unit, perishable) — a parent precedes its children
RESOURCES = [
    ("humanitarian_aid", "Ayuda humanitaria", None, "kits", False),
    ("water", "Agua", "humanitarian_aid", "litros", False),
    ("food", "Alimentos", "humanitarian_aid", "kg", False),
    ("perishable_food", "Alimentos perecederos", "food", "kg", True),
    ("pet_food", "Alimento para mascotas", "food", "kg", False),
    ("medicine", "Medicamentos", "humanitarian_aid", "kits", False),
    ("medical_care", "Atención médica", "medicine", "personas", False),
    ("hygiene", "Aseo e higiene", "humanitarian_aid", "kits", False),
    ("shelter", "Refugio", "humanitarian_aid", "unidades", False),
    ("tents", "Carpas", "shelter", "unidades", False),
    ("bedding", "Colchonetas y cobijas", "shelter", "unidades", False),
    ("clothing", "Ropa", "shelter", "unidades", False),
    ("construction_materials", "Materiales de construcción", None, "unidades", False),
    ("transport", "Transporte", None, "vehículos", False),
    ("machinery", "Maquinaria", None, "unidades", False),
    ("power", "Energía", None, "unidades", False),
    ("generators", "Plantas eléctricas", "power", "unidades", False),
    ("communications", "Comunicaciones", None, "unidades", False),
    ("volunteers", "Voluntarios", None, "personas", False),
    ("rescue", "Rescate", "volunteers", "personas", False),
    ("support", "Apoyo general", "volunteers", "personas", False),
    ("mental_health", "Apoyo psicosocial", None, "personas", False),
    ("cash", "Dinero", None, "COP", False),
    ("collection_point", "Punto de acopio", None, "unidades", False),
]

# Spanish-keyed duplicates, folded into their English key on load
LEGACY_KEYS = {
    "agua": "water",
    "alimentos": "food",
    "alimentos_perecederos": "perishable_food",
    "alimentos_mascotas": "pet_food",
    "medicamentos": "medicine",
    "refugio": "shelter",
    "carpas": "tents",
    "colchonetas": "bedding",
    "transporte": "transport",
    "plantas_electricas": "generators",
    "voluntarios": "volunteers",
}


def load(write: Writer = lambda _: None) -> dict:
    """
    Bring the catalog to the canonical state: create what is missing, adopt what the pipeline
    invented, and retire the Spanish-keyed duplicates.

    Args:
        write (Writer): Progress sink.

    Returns:
        Counts: How many types were created, adopted and retired. All zero on a second run.

    Note:
        This does more than insert because the catalog is shared and long-lived, and a
        duplicate in it is not cosmetic: `agua` beside `water` splits one resource into two
        that never match each other, and the frontend's filter menu shows every entry twice.
    """
    created = 0
    adopted = 0
    regrafted = 0
    by_key: dict[str, ResourceType] = {}

    for key, name, parent_key, unit, perishable in RESOURCES:
        parent = by_key.get(parent_key) if parent_key else None
        resource, was_created = ResourceType.objects.get_or_create(
            key=key,
            defaults={
                "name": name,
                "parent": parent,
                "default_unit": unit,
                "perishable": perishable,
            },
        )
        by_key[key] = resource
        created += int(was_created)
        if was_created:
            continue

        parent_id = parent.id if parent else None
        if resource.name == resource.key:
            resource.name = name
            resource.default_unit = unit
            resource.perishable = perishable
            resource.parent_id = parent_id
            resource.save(update_fields=["name", "parent", "default_unit", "perishable"])
            adopted += 1
        elif resource.parent_id != parent_id:
            resource.parent_id = parent_id
            resource.save(update_fields=["parent"])
            regrafted += 1

    retired = _retire_legacy_keys(by_key)

    write(f"  {created} resource types created, {len(RESOURCES) - created} already present")
    for count, what in (
        (adopted, "auto-created types adopted into the taxonomy"),
        (regrafted, "types moved to the parent this file declares"),
        (retired, "Spanish-keyed duplicates retired"),
    ):
        if count:
            write(f"  {count} {what}")
    return {
        "resource_types": created,
        "adopted": adopted,
        "regrafted": regrafted,
        "retired": retired,
    }


def _retire_legacy_keys(by_key: dict[str, ResourceType]) -> int:
    """
    Fold every legacy Spanish key into its canonical English one.

    Args:
        by_key (dict[str, ResourceType]): The canonical types, freshly loaded.

    Returns:
        int: How many duplicates were removed.

    Note:
        Requirements and child types are repointed before the delete rather than after, because
        `Requirement.resource` is `PROTECT` — a duplicate that anything still references would
        raise instead of merging, and the merge is the whole point.
    """
    retired = 0
    for legacy_key, canonical_key in LEGACY_KEYS.items():
        legacy = ResourceType.objects.filter(key=legacy_key).first()
        canonical = by_key.get(canonical_key)
        if legacy is None or canonical is None:
            continue

        Requirement.objects.filter(resource=legacy).update(resource=canonical)
        ResourceType.objects.filter(parent=legacy).update(parent=canonical)
        legacy.delete()
        retired += 1
    return retired


def clear(write: Writer = lambda _: None) -> int:
    """
    Remove the taxonomy, children before parents.

    Args:
        write (Writer): Progress sink.

    Returns:
        int: Rows removed. A type still referenced by a requirement or still holding children
            is skipped, which is correct — the catalog outlives any one event's data.

    Note:
        Referenced types are excluded by the query rather than left to the database, because
        `Requirement.resource` is `PROTECT`: reaching one raises `ProtectedError` and aborts
        the whole clear, taking the seeds that had not run yet with it.
    """
    removed = 0
    for key, *_ in reversed(RESOURCES):
        deleted, _ = ResourceType.objects.filter(
            key=key, children__isnull=True, requirements__isnull=True
        ).delete()
        removed += deleted

    kept = ResourceType.objects.filter(key__in=[key for key, *_ in RESOURCES]).count()
    write(f"  removed {removed} resource types" + (f", {kept} still in use" if kept else ""))
    return removed
