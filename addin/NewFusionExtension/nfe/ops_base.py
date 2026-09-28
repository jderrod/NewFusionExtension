"""The contract between the engine and Fusion.

``nfe.fusion_ops.FusionOps`` implements it against the real Fusion API;
``nfe.fake_ops.FakeOps`` implements it in memory for tests and simulation.
Every method is called on the Fusion UI thread and must return quickly except
where noted (open/export are inherently blocking in Fusion).
"""


class OpsError(RuntimeError):
    pass


class Ops(object):
    # -- session ---------------------------------------------------------
    def begin_session(self):
        """Remember documents that were open before we started (never touched)."""

    def ready(self):
        """(True, "") when Fusion can do cloud work, else (False, reason).
        Right after Fusion starts it is not signed in / online yet."""
        return True, ""

    # -- cloud files -------------------------------------------------------
    def resolve_file(self, ref):
        """ref = {"id": lineage urn, "name": ...} -> file handle (raise OpsError)."""
        raise NotImplementedError

    def file_version(self, file):
        raise NotImplementedError

    # -- documents ---------------------------------------------------------
    def find_open(self, file):
        """Already-open, visible document for this file, or None."""
        raise NotImplementedError

    def open_document(self, file, visible=True):
        """Blocking open.  Returns a document handle (raise OpsError)."""
        raise NotImplementedError

    def doc_alive(self, doc):
        raise NotImplementedError

    def doc_label(self, doc):
        raise NotImplementedError

    def doc_is_modified(self, doc):
        raise NotImplementedError

    def doc_version(self, doc):
        raise NotImplementedError

    def activate(self, doc):
        raise NotImplementedError

    def close_document(self, doc):
        """Request a close without saving.  Callers verify with doc_alive()."""
        raise NotImplementedError

    def managed_documents(self, keep=()):
        """Open documents that belong to the pipeline (ours, hidden reference
        copies, or copies of configured models/drawings) minus ``keep``."""
        raise NotImplementedError

    def document_count(self):
        raise NotImplementedError

    # -- parameters --------------------------------------------------------
    def read_params(self, doc):
        """-> {name: nfe.params.ModelParam}"""
        raise NotImplementedError

    def apply_params(self, doc, assignments):
        """assignments = [(name, expression)] -> [{name, ok, before, after, message}]"""
        raise NotImplementedError

    def model_warnings(self, doc):
        """Timeline/compute problems after parameters changed -> [str]"""
        return []

    def read_values(self, doc):
        """Computed parameter values after recompute: {name: value}; lengths in
        inches, unitless numbers as floats, text parameters as str."""
        raise NotImplementedError

    # -- manufacture (CAM) -----------------------------------------------------
    def cam_state(self, doc):
        """[{"name", "suppressed", "ops": [{"name", "suppressed", "has_toolpath",
        "valid"}]}] or None when the document has no CAM data."""
        raise NotImplementedError

    def activate_workspace(self, kind):
        """kind: "cam" (MANUFACTURE) or "design"."""
        raise NotImplementedError

    def cam_prepare(self, doc, actions):
        """Apply nfe.cam_rules.resolve_actions() output: unsuppress the chosen
        setups and set each operation's suppression.  Returns a list of changes."""
        raise NotImplementedError

    def cam_generate(self, doc, setup_names):
        """Start (asynchronous) toolpath generation for the active operations in
        these setups.  Returns a token for cam_generate_done(), or None if there
        is nothing to generate."""
        raise NotImplementedError

    def cam_generate_done(self, token):
        raise NotImplementedError

    def cam_problems(self, doc, setup_names):
        """(errors, warnings) for the active operations of these setups:
        missing/invalid toolpaths and generation errors."""
        raise NotImplementedError

    def cam_post(self, doc, setup_name, program_number, out_dir, post_path):
        """Post-process one setup (blocking, short).  Returns the .nc path."""
        raise NotImplementedError

    # -- saving ------------------------------------------------------------
    def start_save(self, doc, message):
        """Start a cloud save.  Returns a token for save_complete(), or None
        when there was nothing to save."""
        raise NotImplementedError

    def save_complete(self, doc, token):
        raise NotImplementedError

    # -- drawings ----------------------------------------------------------
    def update_drawing(self, drawing, model_doc):
        """Refresh the drawing's references (the toolbar 'update' button).
        Returns a dict describing the references."""
        raise NotImplementedError

    def drawing_up_to_date(self, drawing, model_doc):
        raise NotImplementedError

    def export_pdf(self, drawing, path):
        """Blocking export of all sheets to ``path``."""
        raise NotImplementedError

    # -- health --------------------------------------------------------------
    def process_health(self):
        """{"threads", "working_set_mb", "documents"}"""
        raise NotImplementedError

    # -- hub browsing (dashboard) -------------------------------------------
    def hub_list(self, kind, id=None):
        raise NotImplementedError
