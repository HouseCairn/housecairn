# Rooms and items

A home is made of rooms, and a room holds items: the equipment (boiler, smoke
detector, oven) and the surfaces (the north wall, the floor) that you will have to
maintain, repair or replace one day. This guide shows how to describe them. Every
field is optional except the name, so you can start with a name and complete the
rest when you have the document in hand.

## Add the rooms

Open **Rooms** in the navigation, then **Add a room**. Give it a name and, if you
like, a type. Use **Change the order** on the list to put the rooms in the order
you want, for example the way you walk through the house. An area such as the
garden or the cellar is a room like any other.

## Write down the room memory

On the page of a room, **Edit the memory** keeps what you would otherwise search
for later: the paint (colour, brand, reference, batch and the tin left over, chosen from the
[stock](stock.md), whose quantity and place are then shown),
the floor and wall coverings, and free notes. Take photos before closing a wall or a
duct and add them with the category **Behind the walls**: in ten years, they tell
where the cables run and where not to drill. The photos of a room are one list, and
the chips above it keep one category.

## Add the items

**Add an item** on the page of a room. Note the brand, the model, the serial
number, the purchase and installation dates, the warranty, the maintenance contract
(who services it, chosen among the contractors of the home or added on the spot, and its number) and a link to it, and any link worth keeping, such as the manual online. The item
page shows a label that tells whether the warranty is still running.

The **manufacturer's warranty** is apart from the maintenance contract: its duration, in
months or years, who gives it (the manufacturer or the shop, with its name) and the day it
starts, the purchase date when you leave it empty. Housecairn works out the day it ends and
shows it; an item noted before with only an end date keeps that date. **Warranties ending
soon**, on [My home](../screens/my-home.md), lists the items whose warranty ends within 60
days.

If you put an item in the wrong room, edit it and choose another room: the item
moves with its photos.

## Add photos

Photos can be added to a room or to an item, from a phone or a computer. HouseCairn
keeps the picture and nothing else: the location, the date and every other piece of
information hidden in the file are removed, and a large picture is reduced to 2,560
pixels on its longest side. Only JPEG, PNG and WebP are accepted. HEIC pictures,
which some phones save by default, are not accepted: set the camera to its most
compatible format (JPEG) or convert them first.

The default limit is 10 MB per photo. The administrator changes it with the
`HOUSECAIRN_PHOTO_MAX_BYTES` environment variable, in bytes, between 10,240 (10 KB)
and 104,857,600 (100 MB). A reverse proxy in front of HouseCairn may have its own,
lower limit on the size of a request: raise it as well. Photos are stored in the
data volume, in `media/photos/`, and are included in the backup of that volume
(see [Data and backups](../administration/backup.md)).

## Search and share

The search field of the room list finds a room or an item by its name. Everyone in
the home sees the same rooms; read-only members see everything and change nothing.
The address of a room or of an item never changes, whatever you rename or move, and
opens only for signed-in members of the home.

The same data is available to scripts and integrations through the
[API](../api/index.md): rooms, items and photos, with the `read` and `write` scopes
of an API token.
