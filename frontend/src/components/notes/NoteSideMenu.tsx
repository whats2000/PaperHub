/** The notes editor's side menu: BlockNote's own (`SideMenuController`,
 * `SideMenu`, its drag-and-drop and its ⋮⋮ menu), with its two buttons
 * drawn like every other control in the gutter: 16 × 24, a 16 px icon at
 * stroke 2 (14 / 14 px in a narrow pane; see gutter.css). Right to left the
 * 52 px gutter holds the fold toggle (FoldControls.tsx), ⋮⋮ and +, so the
 * menu sits one toggle's width (and a 2 px gap) left of the block.
 */
import { SideMenuExtension, SuggestionMenu } from "@blocknote/core/extensions";
import {
  DragHandleMenu,
  SideMenu,
  SideMenuController,
  useBlockNoteEditor,
  useComponentsContext,
  useDictionary,
  useExtension,
  useExtensionState,
  usePortalElement,
  type SideMenuProps,
} from "@blocknote/react";
import { GripVertical, Plus } from "lucide-react";

/** + : a new line below the block, with the slash menu open (an empty line
 * takes the menu itself), as BlockNote's own button does. */
function AddButton() {
  const Components = useComponentsContext()!;
  const dict = useDictionary();
  const editor = useBlockNoteEditor();
  const suggestions = useExtension(SuggestionMenu);
  const block = useExtensionState(SideMenuExtension, { selector: (s) => s?.block });
  if (block === undefined) return null;
  const onClick = () => {
    const content = block.content;
    if (Array.isArray(content) && content.length === 0) {
      editor.setTextCursorPosition(block);
    } else {
      const line = editor.insertBlocks([{ type: "paragraph" }], block, "after")[0]!;
      editor.setTextCursorPosition(line);
    }
    suggestions.openSuggestionMenu("/");
  };
  return (
    <Components.SideMenu.Button
      className="bn-button note-gutter-btn"
      label={dict.side_menu.add_block_label}
      onClick={onClick}
      icon={<Plus aria-hidden data-test="dragHandleAdd" />}
    />
  );
}

/** ⋮⋮ : drag the block, or click for its menu (BlockNote's). */
function DragButton(props: SideMenuProps) {
  const Components = useComponentsContext()!;
  const dict = useDictionary();
  const sideMenu = useExtension(SideMenuExtension);
  const portalElement = usePortalElement();
  const block = useExtensionState(SideMenuExtension, { selector: (s) => s?.block });
  if (block === undefined) return null;
  const Menu = props.dragHandleMenu ?? DragHandleMenu;
  return (
    <Components.Generic.Menu.Root
      onOpenChange={(open) => (open ? sideMenu.freezeMenu() : sideMenu.unfreezeMenu())}
      position="left"
      portalElement={portalElement}
    >
      <Components.Generic.Menu.Trigger>
        <Components.SideMenu.Button
          className="bn-button note-gutter-btn"
          label={dict.side_menu.drag_handle_label}
          draggable
          onDragStart={(e) => sideMenu.blockDragStart(e, block)}
          onDragEnd={sideMenu.blockDragEnd}
          icon={<GripVertical aria-hidden data-test="dragHandle" />}
        />
      </Components.Generic.Menu.Trigger>
      <Menu />
    </Components.Generic.Menu.Root>
  );
}

function NoteSideMenuView(props: SideMenuProps) {
  return (
    <SideMenu {...props}>
      <AddButton />
      <DragButton {...props} />
    </SideMenu>
  );
}

/** The side menu (pass `sideMenu={false}` to BlockNoteView). */
export function NoteSideMenu() {
  return <SideMenuController sideMenu={NoteSideMenuView} />;
}
