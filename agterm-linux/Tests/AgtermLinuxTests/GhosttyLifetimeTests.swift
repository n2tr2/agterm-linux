import CGtk
import Glibc
import Testing
@testable import AgtermLinux

@Suite("libghostty buffer lifetimes")
struct GhosttyLifetimeTests {
    @Test("surface configuration owns every C buffer until repeated release")
    func configurationStorage() throws {
        let storage = try #require(GhosttySurfaceConfigurationStorage(
            workingDirectory: "/tmp/work",
            command: "/bin/sh -lc true",
            initialInput: "printf ready",
            environment: ["AGTERM_PANE": "main", "AGTERM_SESSION_ID": "session"]
        ))
        var config = ghostty_surface_config_new()
        storage.apply(to: &config)

        #expect(config.working_directory.map { String(cString: $0) } == "/tmp/work")
        #expect(config.command.map { String(cString: $0) } == "/bin/sh -lc true")
        #expect(config.initial_input.map { String(cString: $0) } == "printf ready")
        #expect(config.env_var_count == 2)
        let environment = try #require(config.env_vars)
        let values = Dictionary(uniqueKeysWithValues: UnsafeBufferPointer(start: environment, count: 2).map {
            (String(cString: $0.key), String(cString: $0.value))
        })
        #expect(values == ["AGTERM_PANE": "main", "AGTERM_SESSION_ID": "session"])

        storage.release()
        storage.release()
        #expect(storage.isReleased)
        #expect(storage.environment == nil)
    }

    @Test("partial configuration allocation releases retained buffers")
    func partialAllocation() {
        var allocations = 0
        var deallocations = 0
        let storage = GhosttySurfaceConfigurationStorage(
            workingDirectory: "/tmp",
            command: "false",
            initialInput: nil,
            environment: [:],
            duplicate: { value in
                allocations += 1
                return allocations == 2 ? nil : strdup(value)
            },
            deallocate: { pointer in
                deallocations += 1
                free(pointer)
            }
        )
        #expect(storage == nil)
        #expect(allocations == 2)
        #expect(deallocations == 1)
    }
}
