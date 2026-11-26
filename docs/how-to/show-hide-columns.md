# Show or hide columns

Click Columns above the grid to open the column chooser. There you show or
hide columns and pick the columns for a merge. While you have hidden columns
in the chooser the button says how many, as in Columns (3 hidden). The
chooser lists the columns under their groups. The Item group comes first and
Custom fields last. In between come the groups of the item types, such as
Login or SSH key.

The checkbox on the left of a column shows or hides it. A column that can be
merged also has a box on the right, which selects it for a merge.

![The column chooser with the Login group's checkbox outlined as 1, the checkbox of login.username as 2 and the box on the right of login.username as 3](../images/column-chooser.png)

## Show or hide a column

- Tick or untick the checkbox on the left of a column (2 in the picture) to
  show or hide it. `name` is always shown and its checkbox cannot be changed.
- Tick or untick a group's heading (1 in the picture) to show or hide the
  whole group in one click. The heading is checked while every column of the
  group is shown and unchecked while none is. It is half checked in between,
  and a click then hides the columns still shown.
- Click Show all to bring back every column you hid in the chooser.

Find column narrows the list to the columns whose names hold what you type,
in any letter case. A group's heading then counts and acts on the listed
columns only.

Hide empty columns and Hide rare field columns in the
[toolbar](../reference/ui.md#toolbar) hide columns of their own. The count on
the button and Show all leave those alone.

## Select columns for a merge

The box on the right of a column (3 in the picture) selects it for Merge
selected columns at the bottom of the chooser. It does not show or hide
anything. A column that cannot be merged has no such box. That holds for every
column of the Item group and for a list column such as `login.uris`.

Tick the box on the right of each column you want to merge. The columns may be
in different groups and Find column helps you find them. Merge selected
columns stays off until you have ticked two or more. Click it to open Merge
columns, where you pick the Target column the values move into. For an item
with a value in more than one of the columns you then choose which one it
keeps.

Clear unticks every box on the right. Select for merge in a column's header
menu ticks the same box and Deselect for merge unticks it (see
[Header menu](../reference/ui.md#header-menu)).
