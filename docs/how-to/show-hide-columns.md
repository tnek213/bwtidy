# Show or hide columns

The Columns button above the grid opens the column chooser. It lists every
column under its group (Item, Login, Card, Identity, SSH key, Custom fields).
Each column has two boxes: the checkbox on the left shows or hides it, the box
on the right selects it for a merge.

![The column chooser with the Login group's checkbox outlined as 1, the checkbox of login.username as 2 and the box on the right of login.username as 3](../images/column-chooser.png)

## Show or hide a column

- Tick or untick the checkbox on the left of a column (2 in the picture) to
  show or hide it. `name` can never be hidden.
- Tick or untick a group's heading (1, Login here) to show or hide the whole
  group in one click. It is checked while every column of the group is
  shown, unchecked while none is, and half checked in between.
- Type in Find column to narrow the list. A group's checkbox then acts only
  on the columns the search lists.
- Click Show all to bring every hidden column back.

## Select columns for a merge

The box on the right of a column (3 in the picture) selects it for Merge
selected columns, at the bottom of the chooser. It does not show or hide
anything, and a column that cannot be merged (such as `login.uris`) has
none.

1. Tick the box on the right of each column to merge, two or more. They may
   be in different groups, and Find column helps to find them.
2. Click Merge selected columns. Merge columns opens, where you pick the
   Target column that keeps the values.

Clear unticks every box on the right. Select for merge in a column's header
menu ticks the same box (see [Header menu](../reference/ui.md#header-menu)).
